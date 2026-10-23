"""Clients of TES backends, authenticated as configured for each backend.

proTES authenticates at TES backends with credentials of its own, never with
those of its callers (`auth` of the backends in `custom.tes.service_list`, and
`custom.tes.discovery.auth` for the backends discovered in a registry, cf.
:py:mod:`pro_tes.ga4gh.tes.discovery`):

- `client_credentials`: an access token of proTES, obtained from its identity
  provider with the OAuth 2.0 client credentials grant (RFC 6749, section
  4.4), with the client ID and secret of the environment variables
  `CLIENT_ID_ENV` and `CLIENT_SECRET_ENV`; tokens are cached, and fetched
  anew shortly before they expire;
- `basic`: HTTP Basic credentials of the environment variables named in the
  config (a transitional mode, for backends that don't accept tokens yet);
- `none`: no credentials.

Credentials are looked up for each request, so that long-running Celery
tasks get fresh tokens. Before each request to a discovered backend, its host
is checked to have public addresses only (unless private addresses are
allowed, `custom.tes.discovery.allow_private_addresses`), and redirects are
not followed.
"""

from contextlib import suppress
from dataclasses import dataclass
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import quote_plus

import requests
import tes

from pro_tes.config_models import (
    ClientCredentials,
    DiscoveredBackend,
    Tes,
    TesBackend,
    split_server_url,
)
from pro_tes.exceptions import BackendAuthError, BackendNotConfigured
from pro_tes.ga4gh.tes.discovery import check_public_host, get_backends, url_key
from pro_tes.utils.misc import get_custom_config

logger = logging.getLogger(__name__)

# Environment variables with the client ID and secret of proTES
CLIENT_ID_ENV = "PROTES_CLIENT_ID"
CLIENT_SECRET_ENV = "PROTES_CLIENT_SECRET"  # noqa: S105 (a variable name)
# Path of OpenID Connect discovery documents, relative to the issuer
# Cf. https://openid.net/specs/openid-connect-discovery-1_0.html
OIDC_CONFIG_PATH = ".well-known/openid-configuration"
# Lifetime (in seconds) assumed for tokens of responses without `expires_in`
DEFAULT_TOKEN_LIFETIME = 60
# Timeout (in seconds) of requests to TES backends
TIMEOUT = 5


def get_env(name: str) -> str:
    """Get the value of an environment variable with credentials.

    Args:
        name: Name of the environment variable.

    Returns:
        The value of the environment variable.

    Raises:
        pro_tes.exceptions.BackendAuthError: The variable is not set, or
            empty.
    """
    value = os.environ.get(name)
    if not value:
        raise BackendAuthError(f"Environment variable {name} is not set")
    return value


@dataclass(frozen=True)
class _Token:
    """Cached access token.

    Attributes:
        value: The access token.
        refresh_at: When to fetch a new token (`time.monotonic()`).
        expires_at: When the token expires (`time.monotonic()`).
    """

    value: str
    refresh_at: float
    expires_at: float


class TokenProvider:
    """Access tokens of proTES (client credentials grant), cached.

    Thread-safe; each process (e.g., each Gunicorn or Celery worker) has its
    own cache. Tokens are fetched by one thread at a time, so that threads
    that need a new token at the same time wait for the same fetch instead of
    each fetching one.
    """

    def __init__(self) -> None:
        """Create a provider with empty caches."""
        self._lock = threading.Lock()
        self._tokens: dict[tuple[str, str, str | None], _Token] = {}
        self._token_endpoints: dict[str, str] = {}

    def clear(self) -> None:
        """Remove all cached tokens and token endpoints."""
        with self._lock:
            self._tokens.clear()
            self._token_endpoints.clear()

    def get_token(self, config: Tes) -> str:
        """Get an access token of proTES.

        A cached token is used until `refresh_before` seconds before it
        expires (at most half its lifetime before), then a new one is
        fetched; if that fails, the cached token is used until it expires.

        Args:
            config: TES backend configuration.

        Returns:
            The access token.

        Raises:
            pro_tes.exceptions.BackendAuthError: The client ID or secret is
                not set, or no token was obtained.
        """
        client_id = get_env(CLIENT_ID_ENV)
        client_secret = get_env(CLIENT_SECRET_ENV)
        idp = config.client_credentials
        with self._lock:
            endpoint = self._get_token_endpoint(config=config)
            key = (endpoint, client_id, idp.scope)
            token = self._tokens.get(key)
            if token is not None and time.monotonic() < token.refresh_at:
                return token.value
            try:
                token = self._fetch_token(
                    endpoint=endpoint,
                    client_id=client_id,
                    client_secret=client_secret,
                    idp=idp,
                )
            except BackendAuthError as exc:
                if token is None or time.monotonic() >= token.expires_at:
                    raise
                # the messages of BackendAuthError name endpoints and error
                # types only, never tokens or client secrets
                # nosemgrep  # logger-credential-disclosure: see above
                logger.warning(
                    "Using the cached access token until it expires: %s", exc
                )
                return token.value
            self._tokens[key] = token
            return token.value

    def _get_token_endpoint(self, config: Tes) -> str:
        """Get the token endpoint, from the config or the issuer's discovery.

        Args:
            config: TES backend configuration.

        Returns:
            URL of the token endpoint.

        Raises:
            pro_tes.exceptions.BackendAuthError: Neither token endpoint nor
                issuer is set, or the discovery document can't be fetched,
                is not of the issuer, or has no token endpoint with an
                allowed URL scheme.
        """
        idp = config.client_credentials
        if idp.token_endpoint is not None:
            return idp.token_endpoint
        issuer = idp.issuer
        if issuer is None:
            raise BackendAuthError("No issuer or token endpoint of proTES' client")
        cached = self._token_endpoints.get(issuer)
        if cached is not None:
            return cached
        url = f"{issuer.rstrip('/')}/{OIDC_CONFIG_PATH}"
        try:
            response = requests.get(url, timeout=idp.timeout)
            response.raise_for_status()
            document = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise BackendAuthError(
                f"OpenID Connect discovery document of {issuer!r} unavailable:"
                f" {type(exc).__name__}"
            ) from exc
        if not isinstance(document, dict) or document.get("issuer") != issuer:
            raise BackendAuthError(
                f"OpenID Connect discovery document of {issuer!r} is invalid"
            )
        endpoint = document.get("token_endpoint")
        scheme = None
        if isinstance(endpoint, str):
            with suppress(ValueError):
                scheme = split_server_url(endpoint, "token endpoint").scheme
        if not isinstance(endpoint, str) or scheme not in config.url_schemes:
            raise BackendAuthError(
                f"OpenID Connect discovery document of {issuer!r} has no token"
                f" endpoint with an allowed URL scheme ({config.url_schemes})"
            )
        self._token_endpoints[issuer] = endpoint
        return endpoint

    @staticmethod
    def _fetch_token(
        endpoint: str,
        client_id: str,
        client_secret: str,
        idp: ClientCredentials,
    ) -> _Token:
        """Fetch an access token with the client credentials grant.

        Args:
            endpoint: URL of the token endpoint.
            client_id: Client ID of proTES.
            client_secret: Client secret of proTES.
            idp: Settings of the client of proTES.

        Returns:
            The access token, with the time to fetch a new one.

        Raises:
            pro_tes.exceptions.BackendAuthError: No access token was
                obtained.
        """
        data = {"grant_type": "client_credentials"}
        if idp.scope is not None:
            data["scope"] = idp.scope
        auth: tuple[str, str] | None = None
        if idp.auth_method == "client_secret_basic":
            # encoded as per RFC 6749, section 2.3.1
            auth = (quote_plus(client_id), quote_plus(client_secret))
        else:
            data.update(client_id=client_id, client_secret=client_secret)
        start = time.monotonic()
        try:
            response = requests.post(
                endpoint,
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=idp.timeout,
                # the client credentials must not be sent elsewhere
                allow_redirects=False,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            # without the response body, which may echo the request
            status = exc.response.status_code if exc.response is not None else None
            raise BackendAuthError(
                f"No access token from {endpoint}: {type(exc).__name__}"
                f" (status {status})"
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise BackendAuthError(
                f"No access token from {endpoint}: invalid response"
            ) from exc
        if not isinstance(body, dict):
            raise BackendAuthError(f"No access token from {endpoint}: invalid response")
        value = body.get("access_token")
        token_type = body.get("token_type", "Bearer")
        if not (isinstance(value, str) and value) or (
            not isinstance(token_type, str) or token_type.lower() != "bearer"
        ):
            raise BackendAuthError(
                f"No bearer access token in the response of {endpoint}"
            )
        lifetime = body.get("expires_in")
        if not isinstance(lifetime, int | float) or isinstance(lifetime, bool):
            lifetime = DEFAULT_TOKEN_LIFETIME
        lifetime = max(float(lifetime), 0.0)
        margin = min(float(idp.refresh_before), lifetime / 2)
        return _Token(
            value=value,
            refresh_at=start + lifetime - margin,
            expires_at=start + lifetime,
        )


token_provider = TokenProvider()


def get_backend(url: str) -> TesBackend:
    """Get the configuration of a TES backend.

    If no backend has the URL, and discovery is on, the registry is read
    again (rate-limited), as another process (e.g., the one that forwarded a
    task that the Celery worker tracks) may have discovered the backend
    already.

    Args:
        url: Base URL of the backend; URLs are compared normalized (e.g.,
            ignoring trailing slashes and the case of the host).

    Returns:
        The backend of `custom.tes.service_list`, or discovered in the
        registry, with the URL (cf.
        :py:func:`pro_tes.ga4gh.tes.discovery.get_backends`).

    Raises:
        pro_tes.exceptions.BackendNotConfigured: No backend has the URL.
    """
    key = url_key(url)
    for refresh in (False, True):
        for backend in get_backends(refresh=refresh):
            if url_key(backend.url) == key:
                return backend
        if not get_custom_config().tes.discovery.enabled:
            break
    raise BackendNotConfigured(f"TES backend not configured: {url}")


class TesClient(tes.HTTPClient):
    """Client of a TES backend, authenticated as configured for the backend.

    Credentials are looked up for each request (cf.
    :py:meth:`TesClient._request_params`).

    Attributes:
        backend: Configuration of the backend.
    """

    def __init__(self, url: str, backend: TesBackend, timeout: int = TIMEOUT) -> None:
        """Create a client.

        Args:
            url: URL of the backend, possibly with a base path.
            backend: Configuration of the backend.
            timeout: Timeout of requests, in seconds.
        """
        super().__init__(url=url, timeout=timeout)
        self.backend = backend

    def _request_params(
        self, data: str | None = None, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Compile the parameters of a request, with credentials.

        Args:
            data: JSON body of the request.
            params: Query parameters of the request.

        Returns:
            Keyword arguments of the request (`requests`).

        Raises:
            pro_tes.exceptions.BackendAuthError: No credentials are available.
            pro_tes.exceptions.BackendAddressNotAllowed: The backend was
                discovered, and its host has an address that is not public
                (unless allowed).
        """
        kwargs: dict[str, Any] = super()._request_params(data=data, params=params)
        config = get_custom_config().tes
        if isinstance(self.backend, DiscoveredBackend):
            # redirects could lead to any address
            kwargs["allow_redirects"] = False
            if not config.discovery.allow_private_addresses:
                check_public_host(self.backend.url)
        if self.backend.auth == "client_credentials":
            token = token_provider.get_token(config=config)
            kwargs["headers"]["Authorization"] = f"Bearer {token}"
        elif self.backend.auth == "basic":
            if self.backend.basic is None:
                # prevented by the config validation
                raise BackendAuthError(f"No Basic credentials of {self.backend.url}")
            kwargs["auth"] = (
                get_env(self.backend.basic.username_env),
                get_env(self.backend.basic.password_env),
            )
        return kwargs


def create_client(host: str, base_path: str = "") -> TesClient:
    """Create a client of a configured TES backend.

    Args:
        host: Base URL of the backend, as configured.
        base_path: Path to append to `host`, if any.

    Returns:
        Client of the backend.

    Raises:
        pro_tes.exceptions.BackendNotConfigured: The backend is not
            configured.
        ValueError: The URL is invalid.
    """
    backend = get_backend(url=host)
    url = f"{host.rstrip('/')}/{base_path.strip('/')}"
    return TesClient(url=url, backend=backend)
