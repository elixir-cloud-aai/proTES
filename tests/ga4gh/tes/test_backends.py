"""Tests of the clients of TES backends and their credentials."""

from collections.abc import Iterator
from typing import Any

from connexion import FlaskApp
import pydantic
import pytest
import requests

from pro_tes.config_models import BasicCredentials, ClientCredentials, Tes, TesBackend
from pro_tes.exceptions import BackendAuthError, BackendNotConfigured
from pro_tes.ga4gh.tes import backends
from pro_tes.utils.misc import get_custom_config

ISSUER = "https://idp.example.org/oidc/"
TOKEN_ENDPOINT = "https://idp.example.org/oidc/token"
DISCOVERY_URL = "https://idp.example.org/oidc/.well-known/openid-configuration"
BACKEND_URL = "https://tes.example.org"
# Fictitious client of proTES
CLIENT_ID = "protes client"
CLIENT_SECRET = "s3cret:&"  # noqa: S105 (fictitious)


class FakeResponse:
    """Response of the fake identity provider.

    Attributes:
        body: JSON body, or `None` for an invalid body.
        status_code: HTTP status code.
    """

    def __init__(self, body: Any, status_code: int = 200) -> None:
        """Create a response."""
        self.body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise an error for error status codes, as `requests` does.

        Raises:
            requests.HTTPError: The status code is 400 or higher.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)  # type: ignore[arg-type]

    def json(self) -> Any:
        """Get the JSON body.

        Raises:
            ValueError: The body is invalid.
        """
        if self.body is None:
            raise ValueError("invalid JSON")
        return self.body


class FakeIdp:
    """Fake identity provider: discovery and token endpoint.

    Attributes:
        discovery: Discovery document served.
        token: Token response served.
        token_status: Status of token responses.
        requests: Requests to the token endpoint (keyword arguments).
        discoveries: Number of requests of the discovery document.
    """

    def __init__(self) -> None:
        """Create the identity provider, issuing tokens for 300 seconds."""
        self.discovery: Any = {"issuer": ISSUER, "token_endpoint": TOKEN_ENDPOINT}
        self.token: Any = {
            "access_token": "token-1",
            "token_type": "Bearer",
            "expires_in": 300,
        }
        self.token_status = 200
        self.requests: list[dict[str, Any]] = []
        self.discoveries = 0

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Serve the discovery document.

        Raises:
            requests.ConnectionError: The URL is not the discovery document.
        """
        if url != DISCOVERY_URL:
            raise requests.ConnectionError(url)
        self.discoveries += 1
        return FakeResponse(self.discovery)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Serve the token endpoint; valid tokens are numbered.

        Raises:
            requests.ConnectionError: The URL is not the token endpoint.
        """
        if url != TOKEN_ENDPOINT:
            raise requests.ConnectionError(url)
        self.requests.append(kwargs)
        body = self.token
        if isinstance(body, dict) and body.get("access_token") == "token-1":
            body = {**body, "access_token": f"token-{len(self.requests)}"}
        return FakeResponse(body, status_code=self.token_status)


class Clock:
    """Fake monotonic clock.

    Attributes:
        now: Current time.
    """

    def __init__(self) -> None:
        """Start at 1000."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Get the current time."""
        return self.now


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeIdp]:
    """Fake identity provider, with the client of proTES in the environment.

    The token cache is empty.
    """
    fake = FakeIdp()
    monkeypatch.setattr(backends.requests, "get", fake.get)
    monkeypatch.setattr(backends.requests, "post", fake.post)
    monkeypatch.setenv(backends.CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(backends.CLIENT_SECRET_ENV, CLIENT_SECRET)
    backends.token_provider.clear()
    yield fake
    backends.token_provider.clear()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Fake clock of the token cache."""
    fake = Clock()
    monkeypatch.setattr(backends.time, "monotonic", fake)
    return fake


def tes_config(**idp: Any) -> Tes:
    """Get a TES config with a backend with token authentication.

    Args:
        **idp: Settings of the client of proTES; by default, the issuer.

    Returns:
        TES configuration.
    """
    return Tes(
        service_list=[TesBackend(url=BACKEND_URL)],
        client_credentials=ClientCredentials(**(idp or {"issuer": ISSUER})),
    )


def get_token(config: Tes | None = None) -> str:
    """Get a token of proTES from the shared provider.

    Args:
        config: TES configuration; by default, with the issuer.

    Returns:
        Access token.
    """
    return backends.token_provider.get_token(config=config or tes_config())


def test_token_discovered(idp: FakeIdp, clock: Clock) -> None:
    """Tokens are fetched from the issuer's token endpoint, as the client.

    The client authenticates with HTTP Basic (RFC 6749, section 2.3.1); the
    request is not redirected.
    """
    assert get_token() == "token-1"
    (request,) = idp.requests
    assert request["data"] == {"grant_type": "client_credentials"}
    assert request["auth"] == ("protes+client", "s3cret%3A%26")
    assert request["allow_redirects"] is False
    assert request["timeout"] == 10


def test_token_cached(idp: FakeIdp, clock: Clock) -> None:
    """Tokens are cached, and fetched anew `refresh_before` seconds early."""
    assert get_token() == "token-1"
    clock.now += 239
    assert get_token() == "token-1"
    clock.now += 1
    assert get_token() == "token-2"
    assert len(idp.requests) == 2
    # the discovery document is fetched once
    assert idp.discoveries == 1


def test_token_short_lifetime(idp: FakeIdp, clock: Clock) -> None:
    """Short-lived tokens are fetched anew after half their lifetime."""
    idp.token["expires_in"] = 20
    assert get_token() == "token-1"
    clock.now += 9
    assert get_token() == "token-1"
    clock.now += 1
    assert get_token() == "token-2"


@pytest.mark.parametrize("expires_in", [None, "300", True])
def test_token_lifetime_unknown(
    idp: FakeIdp, clock: Clock, expires_in: Any
) -> None:
    """Tokens without a valid lifetime are taken to expire after 60 seconds."""
    idp.token["expires_in"] = expires_in
    assert get_token() == "token-1"
    clock.now += 29
    assert get_token() == "token-1"
    clock.now += 1
    assert get_token() == "token-2"


def test_token_endpoint_configured(idp: FakeIdp, clock: Clock) -> None:
    """A configured token endpoint is used without discovery; with a scope.

    The client authenticates in the request body (`client_secret_post`).
    """
    config = tes_config(
        token_endpoint=TOKEN_ENDPOINT,
        scope="tes",
        auth_method="client_secret_post",
    )
    assert get_token(config) == "token-1"
    assert idp.discoveries == 0
    (request,) = idp.requests
    assert request["data"] == {
        "grant_type": "client_credentials",
        "scope": "tes",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert request["auth"] is None


@pytest.mark.parametrize("name", [backends.CLIENT_ID_ENV, backends.CLIENT_SECRET_ENV])
def test_token_client_missing(
    idp: FakeIdp, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Without the client ID or secret, there are no tokens."""
    monkeypatch.setenv(name, "")
    with pytest.raises(BackendAuthError, match=name):
        get_token()
    assert not idp.requests


@pytest.mark.parametrize(
    "token, status, match",
    [
        ({"error": "invalid_client"}, 401, "HTTPError \\(status 401\\)"),
        (None, 200, "invalid response"),
        (["token"], 200, "invalid response"),
        ({"token_type": "Bearer"}, 200, "No bearer access token"),
        ({"access_token": ""}, 200, "No bearer access token"),
        ({"access_token": "x", "token_type": "DPoP"}, 200, "No bearer access"),
        ({"access_token": "x", "token_type": 1}, 200, "No bearer access token"),
    ],
)
def test_token_invalid(
    idp: FakeIdp, token: Any, status: int, match: str
) -> None:
    """Errors and invalid responses of the token endpoint give no token."""
    idp.token = token
    idp.token_status = status
    with pytest.raises(BackendAuthError, match=match):
        get_token()


def test_token_endpoint_unreachable(
    idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreachable token endpoints give no token."""

    def post(url: str, **kwargs: Any) -> FakeResponse:
        """Fail to connect."""
        raise requests.ConnectionError(url)

    monkeypatch.setattr(backends.requests, "post", post)
    with pytest.raises(BackendAuthError, match="ConnectionError \\(status None\\)"):
        get_token()


@pytest.mark.parametrize(
    "discovery, match",
    [
        (None, "unavailable"),
        (["document"], "invalid"),
        ({"issuer": "https://other.example.org/"}, "invalid"),
        ({"issuer": ISSUER}, "no token endpoint"),
        ({"issuer": ISSUER, "token_endpoint": 1}, "no token endpoint"),
        (
            {"issuer": ISSUER, "token_endpoint": "http://idp.example.org/token"},
            "no token endpoint with an allowed URL scheme",
        ),
    ],
)
def test_discovery_invalid(idp: FakeIdp, discovery: Any, match: str) -> None:
    """Invalid discovery documents give no token, and are not cached."""
    idp.discovery = discovery
    with pytest.raises(BackendAuthError, match=match):
        get_token()
    with pytest.raises(BackendAuthError, match=match):
        get_token()
    assert idp.discoveries == 2
    assert not idp.requests


def test_no_issuer(idp: FakeIdp) -> None:
    """Without issuer and token endpoint, there are no tokens.

    The config validation prevents this for backends with tokens.
    """
    config = Tes(client_credentials=ClientCredentials())
    with pytest.raises(BackendAuthError, match="No issuer or token endpoint"):
        get_token(config)


@pytest.fixture
def configured(app: FlaskApp) -> Iterator[None]:
    """App context with backends of each authentication mode."""
    with app.app.app_context():
        conf = get_custom_config().tes
        conf.service_list = [
            TesBackend(url=f"{BACKEND_URL}/token/"),
            TesBackend(
                url=f"{BACKEND_URL}/basic",
                auth="basic",
                basic=BasicCredentials(
                    username_env="TES_BASIC_USERNAME", password_env="TES_BASIC_PASSWORD"
                ),
            ),
            TesBackend(url=f"{BACKEND_URL}/none", auth="none"),
        ]
        conf.client_credentials = ClientCredentials(issuer=ISSUER)
        yield


def request_kwargs(host: str) -> dict[str, Any]:
    """Get the keyword arguments of a request of the client of a backend.

    Args:
        host: Base URL of the backend.

    Returns:
        Keyword arguments of the request (`requests`).
    """
    return backends.create_client(host=host)._request_params(data="{}")


def test_client_token(configured: None, idp: FakeIdp) -> None:
    """Backends with `auth: client_credentials` get proTES' bearer token.

    The configured URL is found with or without a trailing slash.
    """
    for host in (f"{BACKEND_URL}/token", f"{BACKEND_URL}/token/"):
        kwargs = request_kwargs(host)
        assert kwargs["headers"]["Authorization"] == "Bearer token-1"
        assert "auth" not in kwargs
        assert kwargs["data"] == "{}"
        assert kwargs["timeout"] == backends.TIMEOUT


def test_client_basic(
    configured: None, idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backends with `auth: basic` get the credentials of the environment."""
    monkeypatch.setenv("TES_BASIC_USERNAME", "protes")
    monkeypatch.setenv("TES_BASIC_PASSWORD", "secret")
    kwargs = request_kwargs(f"{BACKEND_URL}/basic")
    assert kwargs["auth"] == ("protes", "secret")
    assert "Authorization" not in kwargs["headers"]
    assert not idp.requests
    monkeypatch.delenv("TES_BASIC_PASSWORD")
    with pytest.raises(BackendAuthError, match="TES_BASIC_PASSWORD"):
        request_kwargs(f"{BACKEND_URL}/basic")


def test_client_none(configured: None, idp: FakeIdp) -> None:
    """Backends with `auth: none` get no credentials."""
    kwargs = request_kwargs(f"{BACKEND_URL}/none")
    assert "auth" not in kwargs
    assert "Authorization" not in kwargs["headers"]
    assert not idp.requests


def test_client_url(configured: None) -> None:
    """Clients get the URL of the backend, with the base path."""
    client = backends.create_client(host=f"{BACKEND_URL}/none/", base_path="/api/")
    assert client.url == f"{BACKEND_URL}/none/api"
    assert client.backend.auth == "none"


def test_client_not_configured(configured: None) -> None:
    """Backends that are not configured get no client (and no credentials)."""
    with pytest.raises(BackendNotConfigured, match="other.example.org"):
        backends.create_client(host="https://other.example.org")


def test_client_token_request(
    configured: None, idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requests of py-tes carry the token."""
    sent: list[dict[str, Any]] = []

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Record the request, and serve a task."""
        if url == DISCOVERY_URL:
            return idp.get(url)
        sent.append({"url": url, **kwargs})
        return FakeResponse({"id": "remote-1", "state": "QUEUED"})

    monkeypatch.setattr(backends.requests, "get", get)
    task = backends.create_client(host=f"{BACKEND_URL}/token").get_task("remote-1")
    assert task.state == "QUEUED"
    (request,) = sent
    assert request["url"] == f"{BACKEND_URL}/token/ga4gh/tes/v1/tasks/remote-1"
    assert request["headers"]["Authorization"] == "Bearer token-1"


@pytest.mark.parametrize(
    "backend",
    [
        {"url": "tes.example.org"},
        {"url": "https://"},
        {"url": "https://[invalid"},
        {"url": "https://user:secret@tes.example.org"},
        {"url": "https://tes.example.org", "auth": "basic"},
        {
            "url": "https://tes.example.org",
            "basic": {"username_env": "USER", "password_env": "PASSWORD"},
        },
        {
            "url": "https://tes.example.org",
            "auth": "basic",
            "basic": {"username_env": "", "password_env": "PASSWORD"},
        },
    ],
)
def test_backend_config_invalid(backend: dict[str, Any]) -> None:
    """Backends need a URL without credentials, and `basic` for Basic auth."""
    with pytest.raises(pydantic.ValidationError):
        TesBackend(**backend)


def test_backend_config_default() -> None:
    """Backends get proTES' token by default."""
    assert TesBackend(url=BACKEND_URL).auth == "client_credentials"


@pytest.mark.parametrize(
    "config, match",
    [
        ({"service_list": [{"url": "http://tes.example.org"}]}, "not allowed"),
        (
            {
                "service_list": [{"url": BACKEND_URL, "auth": "none"}],
                "client_credentials": {"issuer": "http://idp.example.org/"},
            },
            "not allowed",
        ),
        (
            {"client_credentials": {"token_endpoint": "http://idp.example.org/t"}},
            "not allowed",
        ),
        ({"service_list": [{"url": BACKEND_URL}]}, "need `client_credentials"),
        (
            {
                "service_list": [
                    {"url": BACKEND_URL, "auth": "none"},
                    {"url": f"{BACKEND_URL}/", "auth": "none"},
                ]
            },
            "more than once",
        ),
        ({"url_schemes": []}, "at least 1"),
        ({"url_schemes": ["ftp"]}, "https"),
    ],
)
def test_tes_config_invalid(config: dict[str, Any], match: str) -> None:
    """Backend URLs need allowed schemes; token backends need an issuer."""
    with pytest.raises(pydantic.ValidationError, match=match):
        Tes(**config)


def test_tes_config_http() -> None:
    """URLs with scheme `http` can be allowed (e.g., for local tests)."""
    config = Tes(
        service_list=[{"url": "http://tes.example.org"}],  # type: ignore[list-item]
        url_schemes=["http"],
        client_credentials={"issuer": "http://idp.example.org/"},  # type: ignore[arg-type]
    )
    assert config.service_list[0].url == "http://tes.example.org"
