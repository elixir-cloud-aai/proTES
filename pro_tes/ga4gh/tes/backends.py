"""TES backends of proTES, and their clients, authenticated as configured.

The backends that tasks may be forwarded to are those of the backend list,
`custom.tes.backends` (cf. :py:mod:`foca.plugins.backends`), in order:
configured ones (`type: ga4gh-tes`), and those that service registries list
as verified (`type: ga4gh-service-registry`), which FOCA discovers and
caches per process. Backends at or below the public URL of proTES
(`server.public_url`) are never used, so that proTES does not forward tasks
to itself.

proTES authenticates at TES backends with credentials of its own, never with
those of its callers (`auth` of each entry; discovered backends get that of
their registry entry):

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
is checked to have public addresses only (with
:py:func:`foca.utils.http.check_public_host`; unless private addresses are
allowed, `allow_private_addresses` of the registry entry), and redirects are
not followed. Requests time out after `custom.tes.timeout` seconds (and
after `CONNECT_TIMEOUT` seconds while connecting).

Tasks are created and canceled with a single `POST` request each (which
doesn't follow redirects), to the API path of the backend's URL
(`<url>/ga4gh/tes/v1`), so that backends never get a task twice; tasks are
got from the same path. If it is unknown whether the backend processed such
a request (e.g., because the response timed out), the request is not sent
again (cf. :py:class:`pro_tes.exceptions.BackendOutcomeUnknown`).
"""

from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
import logging
import os
import ssl
import threading
import time
from typing import Any
from urllib.parse import quote, quote_plus

from foca import get_config, get_custom_config
from foca.plugins import Backend, BackendResponse, Headers, OutboundRequest
from foca.plugins.backends import BackendList, RegistryEntry, ServiceEntry
from foca.utils.http import HttpError, check_public_host
import requests
from requests.structures import CaseInsensitiveDict
import tes
from tes.utils import unmarshal
from urllib3.exceptions import ConnectTimeoutError

from pro_tes.config_models import (
    ClientCredentials,
    CustomConfig,
    Tes,
    split_server_url,
)
from pro_tes.exceptions import (
    BackendAddressNotAllowed,
    BackendAuthError,
    BackendNotConfigured,
    BackendOutcomeUnknown,
)

logger = logging.getLogger(__name__)

# Environment variables with the client ID and secret of proTES
CLIENT_ID_ENV = "PROTES_CLIENT_ID"
CLIENT_SECRET_ENV = "PROTES_CLIENT_SECRET"  # noqa: S105 (a variable name)
# Path of OpenID Connect discovery documents, relative to the issuer
# Cf. https://openid.net/specs/openid-connect-discovery-1_0.html
OIDC_CONFIG_PATH = ".well-known/openid-configuration"
# Lifetime (in seconds) assumed for tokens of responses without `expires_in`
DEFAULT_TOKEN_LIFETIME = 60
# Path of the TES API, relative to the URL of a backend
API_PATH = "ga4gh/tes/v1"
# Timeout (in seconds) of connecting to TES backends (at most the timeout of
# requests, `custom.tes.timeout`); short, as requests that time out while
# connecting are sent to the next backend
CONNECT_TIMEOUT = 5
# Errors of `requests` raised before anything is sent
NOT_SENT_ERRORS = (
    requests.ConnectTimeout,
    requests.exceptions.InvalidHeader,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
    requests.exceptions.MissingSchema,
)


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


def get_backend_list(public_url: str | None = None) -> BackendList:
    """Get the backend list of proTES.

    The entries are validated again (a small cost); the backends discovered
    in registries are cached per process. Must be called in an app context.

    Args:
        public_url: Public URL of proTES; by default, `server.public_url`
            of the app's config.

    Returns:
        The backends of `custom.tes.backends`.
    """
    if public_url is None:
        public_url = get_config().server.public_url
    return BackendList(
        get_custom_config(CustomConfig).tes.backends, api="tes", public_url=public_url
    )


def get_backends(refresh: bool = False) -> list[Backend]:
    """Get the TES backends that tasks may be forwarded to, in order.

    Must be called in an app context.

    Args:
        refresh: Whether to read the registries now (rate-limited).

    Returns:
        The configured backends, and those discovered in registries, in the
        order of `custom.tes.backends` (cf.
        :py:meth:`foca.plugins.backends.BackendList.expand`).
    """
    return get_backend_list().expand(refresh=refresh)


def get_backend(url: str) -> tuple[Backend, ServiceEntry | RegistryEntry]:
    """Get a TES backend by its URL, and the entry it comes from.

    If no backend has the URL, the registries are read again (rate-limited),
    as another process (e.g., the one that forwarded a task that the Celery
    worker tracks) may have discovered the backend already. Must be called
    in an app context.

    Args:
        url: Base URL of the backend; URLs are compared normalized (e.g.,
            ignoring trailing slashes and the case of the host).

    Returns:
        The backend, and its entry of `custom.tes.backends` (the registry
        entry, for discovered backends), e.g., with its credentials.

    Raises:
        pro_tes.exceptions.BackendNotConfigured: No backend has the URL.
    """
    backend_list = get_backend_list()
    backend = backend_list.find(url)
    if backend is None:
        raise BackendNotConfigured(f"TES backend not configured: {url}")
    return backend, backend_list.entry_for(backend)


def service_info_fields() -> dict[str, Any]:
    """Get the service info fields with the TES backends of proTES.

    Served with the service info (`service_info.runtime_fields`). Must be
    called in an app context.

    Returns:
        The TES backends (`backends`): identifier, URL, source, group and
        labels of each, without credentials (cf.
        :py:meth:`foca.plugins.backends.BackendList.describe`).
    """
    return {"backends": get_backend_list().describe()}


def _causes(exc: BaseException) -> Iterator[BaseException]:
    """Get an exception and the exceptions it was caused by.

    Args:
        exc: Exception, e.g., of `requests`.

    Yields:
        The exception, and the exceptions it was raised from or during, or
        that it wraps (e.g., the `reason` of errors of `urllib3`).
    """
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or any(current is other for other in seen):
            continue
        seen.append(current)
        yield current
        reason = getattr(current, "reason", None)
        pending.extend(
            [
                current.__cause__,
                current.__context__,
                reason if isinstance(reason, BaseException) else None,
                *(arg for arg in current.args if isinstance(arg, BaseException)),
            ]
        )


def quote_id(task_id: str) -> str:
    """Quote the identifier of a task at a backend, as a path segment.

    Args:
        task_id: Identifier of the task, as the backend set it.

    Returns:
        The identifier, with all characters but unreserved ones
        percent-encoded, also the dots of the identifiers `.` and `..`, so
        that it can't change the path of requests.
    """
    quoted = quote(task_id, safe="")
    if quoted in (".", ".."):
        quoted = quoted.replace(".", "%2E")
    return quoted


def not_sent(exc: requests.RequestException) -> bool:
    """Check whether a failed request was certainly not received.

    Only errors that prevent the connection from being established count:
    the connection was refused, the host was not resolved, the connection
    timed out, or the server's certificate was not accepted; and errors of
    requests that could not be prepared (e.g., invalid headers). For all others
    (e.g., read timeouts, or connections lost after the request was sent),
    the server may have received, and processed, the request.

    Args:
        exc: Error of the request.

    Returns:
        Whether the server certainly did not receive the request.
    """
    if isinstance(exc, NOT_SENT_ERRORS):
        return True
    # `NewConnectionError` (e.g., connection refused, host not resolved) is
    # a subclass of `ConnectTimeoutError`
    return any(
        isinstance(cause, ConnectTimeoutError | ssl.SSLCertVerificationError)
        for cause in _causes(exc)
    )


class TesClient(tes.HTTPClient):
    """Client of a TES backend, authenticated as configured for the backend.

    Credentials are looked up for each request (cf.
    :py:meth:`TesClient._request_params`). Tasks are created, got and
    canceled with a single request each, to the API path of the backend's
    URL (unlike py-tes, which tries other paths too, and so may create a
    task more than once); redirects of `POST` requests are not followed.

    Attributes:
        backend: The backend.
        entry: Entry of the backend in `custom.tes.backends` (the registry
            entry, for discovered backends).
    """

    def __init__(
        self,
        url: str,
        backend: Backend,
        entry: ServiceEntry | RegistryEntry,
        timeout: int | None = None,
    ) -> None:
        """Create a client.

        Args:
            url: URL of the backend, possibly with a base path.
            backend: The backend.
            entry: Entry of the backend in `custom.tes.backends` (the
                registry entry, for discovered backends), with its
                credentials.
            timeout: Timeout of requests, in seconds; by default
                `custom.tes.timeout`.
        """
        if timeout is None:
            timeout = get_custom_config(CustomConfig).tes.timeout
        super().__init__(url=url, timeout=timeout)
        self.backend = backend
        self.entry = entry

    def forward(
        self,
        outbound: OutboundRequest,
        task: tes.Task,
        connect_timeout: float | None = None,
    ) -> tuple[str, BackendResponse]:
        """Relay a task, with a single request, as the plugins prepared it.

        The request has the method, path (below the URL of the backend),
        query and headers of the outbound request (cf.
        :py:meth:`foca.plugins.PipelineRun.attempts`; usually `POST
        /ga4gh/tes/v1/tasks`), the task as body, and the credentials of the
        backend (none if the plugins set `outbound.auth` to `none`; plugins
        can't choose other credentials), added after the plugins ran.

        Args:
            outbound: Outbound request, after the `forward` phase.
            task: Task to relay (cf. :py:func:`pro_tes.gateway.relayed_task`).
            connect_timeout: Timeout of connecting, in seconds, if shorter
                than `CONNECT_TIMEOUT` (e.g., the time left to forward the
                task).

        Returns:
            Identifier of the task at the backend, and the response of the
            backend.

        Raises:
            requests.RequestException: The backend did not accept the task:
                it was not reached (cf. :py:func:`not_sent`), or it rejected
                the task (e.g., with a client error).
            pro_tes.exceptions.BackendAuthError: No credentials are
                available.
            pro_tes.exceptions.BackendOutcomeUnknown: The backend may have
                accepted the task, but did not confirm it with its
                identifier.
        """
        response = self._send(
            method=outbound.method,
            url=f"{self.url}{outbound.path}",
            data=task.as_json(),
            params=outbound.query,
            headers=dict(outbound.headers),
            # plugins may drop the credentials of the backend, but not
            # choose others (e.g., send proTES's token to a backend that
            # gets none)
            auth="none" if outbound.auth == "none" else None,
            connect_timeout=connect_timeout,
        )
        try:
            body = response.json()
        except (ValueError, RecursionError):
            # e.g., invalid or too deeply nested JSON: the outcome is unknown
            body = None
        task_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise BackendOutcomeUnknown(
                f"TES backend {self.url} accepted the task without an identifier",
                reason="no_task_id",
            )
        return task_id, BackendResponse(
            status=response.status_code,
            headers=response_headers(response),
            payload=body,
        )

    def get_task(self, task_id: str, view: str = "BASIC") -> tes.Task:
        """Get a task (`GET /tasks/{id}`), with a single request.

        Args:
            task_id: Identifier of the task at the backend.
            view: View of the task (`MINIMAL`, `BASIC` or `FULL`).

        Returns:
            The task.

        Raises:
            requests.RequestException: The request failed, or the backend
                answered with an error.
            pro_tes.exceptions.BackendAuthError: No credentials are
                available.
        """
        kwargs = self._request_params(params={"view": view})
        timeout = kwargs.pop("timeout")
        url = f"{self.url}/{API_PATH}/tasks/{quote_id(task_id)}"
        response = requests.get(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        task: tes.Task = unmarshal(response.json(), tes.Task)
        return task

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task (`POST /tasks/{id}:cancel`), with a single request.

        Args:
            task_id: Identifier of the task at the backend.

        Raises:
            requests.RequestException: The backend was not reached (cf.
                :py:func:`not_sent`), or it rejected the request.
            pro_tes.exceptions.BackendAuthError: No credentials are
                available.
            pro_tes.exceptions.BackendOutcomeUnknown: The backend may have
                canceled the task, but did not confirm it.
        """
        self._send(
            method="POST", url=f"{self.url}/{API_PATH}/tasks/{quote_id(task_id)}:cancel"
        )

    def _send(
        self,
        method: str,
        url: str,
        data: str | None = None,
        params: dict[str, list[str]] | None = None,
        headers: dict[str, str] | None = None,
        auth: str | None = None,
        connect_timeout: float | None = None,
    ) -> requests.Response:
        """Send a request once, e.g., a `POST` request to create a task.

        Redirects are not followed.

        Args:
            method: HTTP method.
            url: URL of the request.
            data: JSON body of the request.
            params: Query parameters of the request.
            headers: Headers of the request; credentials and the media type
                of the body are set after them.
            auth: Name of the credentials to use; `None` for those of the
                backend.
            connect_timeout: Timeout of connecting, in seconds, if shorter
                than `CONNECT_TIMEOUT`.

        Returns:
            The response, with a success status (2xx).

        Raises:
            requests.RequestException: The request was certainly not
                processed: it was not received (cf. :py:func:`not_sent`), or
                it was answered with a status other than 2xx and 5xx (e.g.,
                a client error, or a redirect, which is not followed).
            pro_tes.exceptions.BackendAuthError: No credentials are
                available.
            pro_tes.exceptions.BackendOutcomeUnknown: The request may have
                been processed: it failed after it may have been received
                (e.g., the response timed out), or it was answered with a
                server error (5xx).
        """
        deadline = (
            None if connect_timeout is None else time.monotonic() + connect_timeout
        )
        # may raise before anything is sent, e.g., without credentials
        kwargs = self._request_params(data=data, params=params, auth=auth)
        connect, read = kwargs.pop("timeout")
        if deadline is not None:
            # getting a token may have taken some of the time
            left = deadline - time.monotonic()
            if left <= 0:
                raise requests.ConnectTimeout(f"No time left to connect to {url}")
            connect = min(connect, left)
        merged = CaseInsensitiveDict(headers or {})
        merged.update(kwargs["headers"])
        kwargs["headers"] = merged
        # e.g., redirects of authentication gateways would turn requests
        # into `GET` requests, whose answers could be taken for confirmations
        kwargs["allow_redirects"] = False
        try:
            response = requests.request(method, url, timeout=(connect, read), **kwargs)
        except requests.RequestException as exc:
            if not_sent(exc):
                raise
            reason = (
                "read_timeout"
                if isinstance(exc, requests.ReadTimeout)
                else "connection_lost"
            )
            raise BackendOutcomeUnknown(
                f"Outcome of {method} {url} unknown: {type(exc).__name__}",
                reason=reason,
            ) from exc
        if 200 <= response.status_code < 300:
            return response
        if response.status_code >= 500:
            # e.g., gateways answer 502, 503 or 504 also when the backend
            # behind them got the request
            raise BackendOutcomeUnknown(
                f"Outcome of {method} {url} unknown: status {response.status_code}",
                reason=f"http_{response.status_code}",
            )
        raise requests.HTTPError(
            f"{method} {url} failed: status {response.status_code}",
            response=response,
        )

    def _request_params(
        self,
        data: str | None = None,
        params: dict[str, Any] | None = None,
        auth: str | None = None,
    ) -> dict[str, Any]:
        """Compile the parameters of a request, with credentials.

        Args:
            data: JSON body of the request.
            params: Query parameters of the request.
            auth: Name of the credentials to use (`client_credentials`,
                `basic` or `none`); `None` for those of the backend.

        Returns:
            Keyword arguments of the request (`requests`), with the timeouts
            of connecting (`CONNECT_TIMEOUT`, at most `timeout`) and of the
            request (`timeout`).

        Raises:
            pro_tes.exceptions.BackendAuthError: No credentials are available.
            pro_tes.exceptions.BackendAddressNotAllowed: The backend was
                discovered, and its host has an address that is not public
                (unless allowed), or can't be resolved.
        """
        kwargs: dict[str, Any] = super()._request_params(data=data, params=params)
        kwargs["timeout"] = (min(CONNECT_TIMEOUT, self.timeout), self.timeout)
        config = get_custom_config(CustomConfig).tes
        if isinstance(self.entry, RegistryEntry):
            # redirects could lead to any address
            kwargs["allow_redirects"] = False
            if not self.entry.allow_private_addresses:
                try:
                    check_public_host(self.backend.url)
                except HttpError as exc:
                    raise BackendAddressNotAllowed(str(exc)) from exc
        if auth is None:
            auth = self.backend.auth
        if auth == "client_credentials":
            token = token_provider.get_token(config=config)
            kwargs["headers"]["Authorization"] = f"Bearer {token}"
        elif auth == "basic":
            basic = self.entry.basic if isinstance(self.entry, ServiceEntry) else None
            if basic is None:
                # prevented by the config validation
                raise BackendAuthError(f"No Basic credentials of {self.backend.url}")
            kwargs["auth"] = (get_env(basic.username_env), get_env(basic.password_env))
        elif auth != "none":
            raise BackendAuthError(
                f"Unknown credentials {auth!r} of {self.backend.url}"
            )
        return kwargs


def response_headers(response: requests.Response) -> Headers:
    """Get the headers of a response, for the plugins of the `response` phase.

    Headers that FOCA doesn't accept (e.g., with NUL characters) are left
    out: the backend accepted the task, so they must not fail the attempt.

    Args:
        response: Response of a backend.

    Returns:
        The headers of the response.
    """
    headers = Headers()
    for name, value in response.headers.items():
        try:
            headers[name] = value
        except (TypeError, ValueError):
            logger.warning("Header %r of the response of a backend left out", name)
    return headers


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
    backend, entry = get_backend(url=host)
    url = f"{host.rstrip('/')}/{base_path.strip('/')}"
    return TesClient(url=url, backend=backend, entry=entry)


def client_for(backend: Backend) -> TesClient:
    """Create a client of a candidate backend, e.g., of a pipeline run.

    Args:
        backend: Backend, as expanded from `custom.tes.backends` (cf.
            :py:func:`get_backends`).

    Returns:
        Client of the backend, with the credentials of its entry.

    Raises:
        pro_tes.exceptions.BackendNotConfigured: No entry of
            `custom.tes.backends` yields the backend (e.g., the list changed
            meanwhile).
    """
    try:
        entry = get_backend_list().entry_for(backend)
    except KeyError as exc:
        raise BackendNotConfigured(
            f"TES backend not configured: {backend.url}"
        ) from exc
    return TesClient(url=backend.url, backend=backend, entry=entry)


# Reason codes of errors of attempts to relay tasks, by error class; the
# first match counts
_REASONS: tuple[tuple[type[Exception], str], ...] = (
    (BackendAuthError, "no_credentials"),
    (BackendNotConfigured, "unknown_backend"),
    (ValueError, "invalid_backend"),
    (BackendAddressNotAllowed, "address_not_allowed"),
    (requests.ConnectTimeout, "connect_timeout"),
)


def forward_reason(exc: Exception) -> str:
    """Get the reason code of a failed attempt to relay a task.

    Args:
        exc: Error of the attempt (cf. :py:meth:`TesClient.forward`).

    Returns:
        Reason code (cf. :py:meth:`foca.plugins.Attempt.failed`), e.g.,
        `no_credentials`, `address_not_allowed`, `connect_timeout`,
        `connect_error`, `http_403`, or that of an unknown outcome (cf.
        :py:class:`pro_tes.exceptions.BackendOutcomeUnknown`).
    """
    if isinstance(exc, BackendOutcomeUnknown):
        return exc.reason
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return "rejected" if response is None else f"http_{response.status_code}"
    for cls, reason in _REASONS:
        if isinstance(exc, cls):
            return reason
    return "connect_error"
