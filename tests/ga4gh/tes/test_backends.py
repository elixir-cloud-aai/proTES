"""Tests of the clients of TES backends and their credentials."""

from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import threading
from typing import Any, ClassVar

from connexion import FlaskApp
import pydantic
import pytest
import requests
import tes
from urllib3.exceptions import (
    MaxRetryError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
    SSLError,
)

from pro_tes.config_models import BasicCredentials, ClientCredentials, Tes, TesBackend
from pro_tes.exceptions import (
    BackendAuthError,
    BackendNotConfigured,
    BackendOutcomeUnknown,
)
from pro_tes.ga4gh.tes import backends
from pro_tes.utils.misc import get_custom_config

ISSUER = "https://idp.example.org/oidc/"
TOKEN_ENDPOINT = "https://idp.example.org/oidc/token"  # noqa: S105 (a URL)
DISCOVERY_URL = "https://idp.example.org/oidc/.well-known/openid-configuration"
BACKEND_URL = "https://tes.example.org"
# Fictitious client of proTES
CLIENT_ID = "protes client"
CLIENT_SECRET = "s3cret:&"  # noqa: S105 (fictitious)
# Connecting of sockets, as before the tests block network access
_CONNECT = socket.socket.connect


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


def test_token_refresh_failed(idp: FakeIdp, clock: Clock) -> None:
    """If a new token can't be fetched, the cached one is used until it expires."""
    assert get_token() == "token-1"
    idp.token_status = 503
    clock.now += 240
    assert get_token() == "token-1"
    clock.now += 59
    assert get_token() == "token-1"
    clock.now += 1
    with pytest.raises(BackendAuthError, match="status 503"):
        get_token()
    idp.token_status = 200
    assert get_token() == "token-5"


def test_token_short_lifetime(idp: FakeIdp, clock: Clock) -> None:
    """Short-lived tokens are fetched anew after half their lifetime."""
    idp.token["expires_in"] = 20
    assert get_token() == "token-1"
    clock.now += 9
    assert get_token() == "token-1"
    clock.now += 1
    assert get_token() == "token-2"


@pytest.mark.parametrize("expires_in", [None, "300", True])
def test_token_lifetime_unknown(idp: FakeIdp, clock: Clock, expires_in: Any) -> None:
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
def test_token_invalid(idp: FakeIdp, token: Any, status: int, match: str) -> None:
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
        ({"issuer": ISSUER, "token_endpoint": "https:///token"}, "no token endpoint"),
        (
            {"issuer": ISSUER, "token_endpoint": "https://[idp.example.org/token"},
            "no token endpoint",
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
                    username_env="TES_BASIC_USERNAME",
                    password_env="TES_BASIC_PASSWORD",  # noqa: S106 (a name)
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
        assert kwargs["timeout"] == (5, 15)


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


def test_client_basic_unset(configured: None) -> None:
    """Backends with `auth: basic`, but no `basic` (not validated), get none."""
    backend = TesBackend.model_construct(url=BACKEND_URL, auth="basic", basic=None)
    client = backends.TesClient(url=BACKEND_URL, backend=backend)
    with pytest.raises(BackendAuthError, match="No Basic credentials"):
        client._request_params()


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
    with pytest.raises(BackendNotConfigured, match=r"other\.example\.org"):
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
        {"url": "https://tes.example.org:bad"},
        {"url": "https://tes.example.org:65536"},
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
        (
            {"client_credentials": {"issuer": "https:///realms/protes"}},
            "issuer of `client_credentials` without scheme or host",
        ),
        (
            {"client_credentials": {"token_endpoint": "https://idp.example.org:x/t"}},
            "Invalid URL of the token endpoint",
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


NONE_URL = f"{BACKEND_URL}/none"
TASK = tes.Task(executors=[tes.Executor(image="alpine", command=["true"])])


class FakePost:
    """Fake `requests.post`: records requests, and answers or fails.

    Attributes:
        sent: URLs and keyword arguments of the requests.
        answer: Response to serve, or exception to raise.
    """

    def __init__(self, answer: FakeResponse | Exception) -> None:
        """Create the fake."""
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.answer = answer

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record the request, and serve the answer.

        Raises:
            Exception: The answer is an exception.
        """
        self.sent.append((url, kwargs))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def post(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> Callable[[FakeResponse | Exception], FakePost]:
    """Factory of fake `requests.post`s (installed when created)."""

    def install(answer: FakeResponse | Exception) -> FakePost:
        """Install a fake `requests.post` with an answer."""
        fake = FakePost(answer)
        monkeypatch.setattr(backends.requests, "post", fake)
        return fake

    return install


def wrapped(reason: Exception, error: type[requests.RequestException]) -> Exception:
    """Get an error of `requests`, as raised for a reason of `urllib3`.

    Args:
        reason: Error of `urllib3`.
        error: Class of the error of `requests`.

    Returns:
        The error, wrapping `MaxRetryError` with the reason, as `requests`
        raises it.
    """
    return error(MaxRetryError(None, "/", reason=reason))  # type: ignore[arg-type]


def test_client_timeout(configured: None) -> None:
    """Requests to backends time out after `custom.tes.timeout` seconds.

    Connecting times out after 5 seconds, at most.
    """
    get_custom_config().tes.timeout = 120
    assert request_kwargs(NONE_URL)["timeout"] == (5, 120)
    client = backends.TesClient(
        url=BACKEND_URL, backend=TesBackend(url=BACKEND_URL, auth="none"), timeout=3
    )
    assert client._request_params()["timeout"] == (3, 3)


def test_create_task_once(post: Callable[..., FakePost]) -> None:
    """Tasks are created with a single request, to the API path."""
    fake = post(FakeResponse({"id": "remote-1"}))
    client = backends.create_client(host=f"{NONE_URL}/")
    assert client.create_task(TASK) == "remote-1"
    ((url, kwargs),) = fake.sent
    assert url == f"{NONE_URL}/ga4gh/tes/v1/tasks"
    assert kwargs["data"] == TASK.as_json()
    assert kwargs["timeout"] == (5, 15)
    assert kwargs["allow_redirects"] is False


def test_cancel_task_once(post: Callable[..., FakePost]) -> None:
    """Tasks are canceled with a single request; their identifier is quoted."""
    fake = post(FakeResponse({}))
    backends.create_client(host=NONE_URL).cancel_task("remote/1?x")
    ((url, kwargs),) = fake.sent
    assert url == f"{NONE_URL}/ga4gh/tes/v1/tasks/remote%2F1%3Fx:cancel"
    assert "data" not in kwargs
    assert kwargs["allow_redirects"] is False


@pytest.fixture
def loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow connections to local servers (the tests block network access).

    Requests go there directly, also behind proxies.
    """
    blocked = socket.socket.connect

    def connect(sock: socket.socket, address: Any) -> None:
        """Connect to local addresses only (else as blocked)."""
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            _CONNECT(sock, address)
            return
        blocked(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1")


class RedirectingHandler(BaseHTTPRequestHandler):
    """Handler of a local server that redirects `POST` requests.

    `GET` requests (e.g., of followed redirects) get a list of tasks, as if
    they were confirmations.

    Attributes:
        methods: Methods of the requests received.
    """

    methods: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        """Redirect."""
        self.methods.append("POST")
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Answer with an empty list of tasks."""
        self.methods.append("GET")
        body = b'{"id": "login", "tasks": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Log nothing."""


def test_post_redirect_not_followed(
    configured: None, loopback: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redirects of `POST` requests are not followed, but are rejections.

    E.g., of authentication gateways, whose login page would be taken for
    a confirmation.
    """
    monkeypatch.setattr(RedirectingHandler, "methods", [])
    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        get_custom_config().tes.service_list.append(TesBackend(url=url, auth="none"))
        client = backends.create_client(host=url)
        with pytest.raises(requests.HTTPError, match="status 302"):
            client.create_task(TASK)
        with pytest.raises(requests.HTTPError, match="status 302"):
            client.cancel_task("remote-1")
    finally:
        server.shutdown()
        server.server_close()
    assert RedirectingHandler.methods == ["POST", "POST"]


def test_get_task_once(
    configured: None, idp: FakeIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tasks are got with a single request; their identifier is quoted."""
    sent: list[str] = []

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Record the request, and fail."""
        sent.append(url)
        return FakeResponse({}, status_code=404)

    monkeypatch.setattr(backends.requests, "get", get)
    with pytest.raises(requests.HTTPError):
        backends.create_client(host=NONE_URL).get_task("remote/1")
    assert sent == [f"{NONE_URL}/ga4gh/tes/v1/tasks/remote%2F1"]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_post_server_error(post: Callable[..., FakePost], status: int) -> None:
    """Server errors leave the outcome unknown; requests aren't sent again."""
    fake = post(FakeResponse({"id": "remote-1"}, status_code=status))
    client = backends.create_client(host=NONE_URL)
    with pytest.raises(BackendOutcomeUnknown, match=f"status {status}"):
        client.create_task(TASK)
    with pytest.raises(BackendOutcomeUnknown):
        client.cancel_task("remote-1")
    assert len(fake.sent) == 2


def create(client: backends.TesClient) -> None:
    """Create a task."""
    client.create_task(TASK)


def cancel(client: backends.TesClient) -> None:
    """Cancel a task."""
    client.cancel_task("remote-1")


@pytest.mark.parametrize("status", [302, 400, 401, 403, 404])
@pytest.mark.parametrize("action", [create, cancel])
def test_post_rejected(
    post: Callable[..., FakePost],
    status: int,
    action: Callable[[backends.TesClient], None],
) -> None:
    """Other errors (and redirects) are rejections; requests aren't sent again."""
    fake = post(FakeResponse({}, status_code=status))
    with pytest.raises(requests.HTTPError, match=f"status {status}") as info:
        action(backends.create_client(host=NONE_URL))
    assert info.value.response.status_code == status
    assert len(fake.sent) == 1


@pytest.mark.parametrize("body", [None, [], {}, {"id": ""}, {"id": 1}])
def test_create_task_no_id(post: Callable[..., FakePost], body: Any) -> None:
    """Tasks accepted without an identifier have an unknown outcome."""
    post(FakeResponse(body))
    with pytest.raises(BackendOutcomeUnknown, match="without an identifier"):
        backends.create_client(host=NONE_URL).create_task(TASK)


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectTimeout("connect timeout"),
        wrapped(NewConnectionError(None, "refused"), requests.ConnectionError),  # type: ignore[arg-type]
        wrapped(
            NameResolutionError("tes.example.org", None, socket.gaierror()),  # type: ignore[arg-type]
            requests.ConnectionError,
        ),
        wrapped(
            SSLError(ssl.SSLCertVerificationError("certificate verify failed")),
            requests.exceptions.SSLError,
        ),
        requests.exceptions.InvalidHeader("invalid header"),
    ],
)
@pytest.mark.parametrize("action", [create, cancel])
def test_post_not_sent(
    post: Callable[..., FakePost],
    error: Exception,
    action: Callable[[backends.TesClient], None],
) -> None:
    """Requests that certainly weren't received fail as they are."""
    fake = post(error)
    with pytest.raises(type(error)):
        action(backends.create_client(host=NONE_URL))
    assert len(fake.sent) == 1


@pytest.mark.parametrize(
    "error",
    [
        requests.ReadTimeout("read timeout"),
        wrapped(
            ReadTimeoutError(None, "/", "read timed out"),  # type: ignore[arg-type]
            requests.ConnectionError,
        ),
        requests.ConnectionError(
            ProtocolError("Connection aborted.", ConnectionResetError())
        ),
        wrapped(SSLError("EOF occurred"), requests.exceptions.SSLError),
        requests.exceptions.ChunkedEncodingError("incomplete"),
    ],
)
def test_post_maybe_received(post: Callable[..., FakePost], error: Exception) -> None:
    """Requests that may have been received have an unknown outcome."""
    fake = post(error)
    with pytest.raises(BackendOutcomeUnknown, match=type(error).__name__) as info:
        backends.create_client(host=NONE_URL).create_task(TASK)
    assert info.value.__cause__ is error
    assert len(fake.sent) == 1


def test_post_no_credentials(post: Callable[..., FakePost]) -> None:
    """Without credentials, nothing is sent."""
    fake = post(FakeResponse({"id": "remote-1"}))
    with pytest.raises(BackendAuthError):
        backends.create_client(host=f"{BACKEND_URL}/basic").create_task(TASK)
    assert not fake.sent


def test_not_sent_refused(loopback: None) -> None:
    """Refused connections are recognized, as `requests` raises them.

    The port is free, unless another process took it in the meantime.
    """
    with socket.socket() as sock:
        # a port that nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        with pytest.raises(requests.ConnectionError, match="refused") as info:
            requests.post(f"http://127.0.0.1:{port}/", timeout=5)
    assert backends.not_sent(info.value)


@pytest.mark.parametrize(
    "task_id, quoted",
    [
        ("remote-1", "remote-1"),
        ("a/b?c#d", "a%2Fb%3Fc%23d"),
        (".", "%2E"),
        ("..", "%2E%2E"),
        ("...", "..."),
    ],
)
def test_quote_id(task_id: str, quoted: str) -> None:
    """Identifiers of tasks can't change the path of requests."""
    assert backends.quote_id(task_id) == quoted


def test_not_sent_cyclic() -> None:
    """Exceptions whose causes form a cycle are checked once each."""
    first = requests.ConnectionError("first")
    second = ValueError("second")
    first.__cause__ = second
    second.__context__ = first
    assert not backends.not_sent(first)
