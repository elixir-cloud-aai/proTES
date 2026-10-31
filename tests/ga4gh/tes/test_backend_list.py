"""Tests of the backend list of proTES: configured and discovered TES backends.

Discovery itself (registry reads, selection, caching) is FOCA's
(:py:mod:`foca.plugins.backends`) and tested there; these tests cover how
proTES uses it, with a fake registry and fake DNS.
"""

from collections.abc import Iterator
import socket
import ssl
import threading
from typing import Any
from urllib.parse import urlsplit

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_config, get_custom_config
from foca.plugins import OutboundRequest
from foca.plugins.backends import BackendListConfig, registry
from foca.utils import http
from foca.utils.http import AddressNotAllowed, HostNotResolved
import pytest
import requests
import tes
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from pro_tes.config_models import ClientCredentials, CustomConfig
from pro_tes.exceptions import (
    BackendAddressNotAllowed,
    BackendNotConfigured,
    BackendOutcomeUnknown,
    BackendRequestNotSent,
)
from pro_tes.ga4gh.tes import backends
from tests.ga4gh.tes.conftest import BASE_PATH, TASK, FakeBackend, relayed

REGISTRY = "https://registry.example.org/ga4gh/registry/v1"
TES_TYPE = {"group": "org.ga4gh", "artifact": "tes", "version": "1.1.0"}
STATIC = "https://static.example.org"
# Public address of the fake DNS (documentation addresses are not public)
PUBLIC_IPV4 = "93.184.215.14"


class FakeRegistry:
    """Fake service registry, with fake DNS for the hosts of its services.

    Attributes:
        services: Services listed (`GET /services`).
        verifications: Verifications listed (`GET /verifications`).
        hosts: Addresses of host names.
        reads: URLs read.
    """

    def __init__(self) -> None:
        """Create a registry without services."""
        self.services: list[dict[str, Any]] = []
        self.verifications: list[dict[str, Any]] = []
        self.hosts: dict[str, list[str]] = {}
        self.reads: list[str] = []

    def add(self, service_id: str, url: str, verified: bool = True) -> None:
        """Register a TES service, with a public address for its host.

        Args:
            service_id: Identifier of the service.
            url: URL of the service.
            verified: Whether the service is verified.
        """
        self.services.append(
            {"id": service_id, "name": service_id, "url": url, "type": TES_TYPE}
        )
        if verified:
            self.verifications.append(
                {
                    "service_id": service_id,
                    "url": url,
                    "type": TES_TYPE,
                    "verified_at": "2026-10-29T12:34:56.789Z",
                }
            )
        self.hosts.setdefault(urlsplit(url).hostname or "", [PUBLIC_IPV4])

    def fetch_json(self, url: str, timeout: float, **kwargs: Any) -> Any:
        """Serve the services or verifications.

        Args:
            url: URL.
            timeout: Timeout; ignored.
            **kwargs: Options of the request; ignored.

        Returns:
            The services or verifications.
        """
        self.reads.append(url)
        if url == f"{REGISTRY}/services":
            return self.services
        assert url == f"{REGISTRY}/verifications"
        return self.verifications

    def check_public_host(self, url: str, timeout: float = 5) -> None:
        """Check that a host has public addresses only, with the fake DNS.

        Args:
            url: URL.
            timeout: Timeout; ignored.

        Raises:
            foca.utils.http.HostNotResolved: The host is unknown.
            foca.utils.http.AddressNotAllowed: An address is not public.
        """
        host = urlsplit(url).hostname or ""
        if host not in self.hosts:
            raise HostNotResolved(f"Host of {url} not resolved")
        if self.hosts[host] != [PUBLIC_IPV4]:
            raise AddressNotAllowed(f"Host of {url} has an address that is not public")


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
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    """Fake registry and DNS, with a fake clock of the registry caches."""
    fake = FakeRegistry()
    monkeypatch.setattr(registry, "fetch_json", fake.fetch_json)
    monkeypatch.setattr(registry, "check_public_host", fake.check_public_host)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Fake clock of the registry caches."""
    fake = Clock()
    monkeypatch.setattr(registry.time, "monotonic", fake)
    return fake


def backend_list(**registry_settings: Any) -> BackendListConfig:
    """Get a backend list: a configured backend, then the fake registry.

    Args:
        **registry_settings: Settings of the registry entry; by default,
            `auth: none`.

    Returns:
        Backend list (`custom.tes.backends`).
    """
    return BackendListConfig.model_validate(
        [
            {"type": "ga4gh-tes", "url": f"{STATIC}/", "auth": "none"},
            {
                "type": "ga4gh-service-registry",
                "id": "registry",
                "url": REGISTRY,
                "auth": "none",
                **registry_settings,
            },
        ]
    )


@pytest.fixture
def discovering(
    app: FlaskApp, fake_registry: FakeRegistry, clock: Clock
) -> Iterator[FakeRegistry]:
    """App context with a configured backend and the fake registry."""
    with app.app.app_context():
        get_custom_config(CustomConfig).tes.backends = backend_list()
        yield fake_registry


def test_get_backends(discovering: FakeRegistry) -> None:
    """Backends come in the order of the list; configured ones take precedence."""
    discovering.add("a", f"{STATIC.upper()}/ga4gh/tes/v1")
    discovering.add("b", "https://tes1.example.org")
    discovering.add("u", "https://unverified.example.org", verified=False)
    found = backends.get_backends()
    assert [backend.url for backend in found] == [STATIC, "https://tes1.example.org"]
    assert [backend.source for backend in found] == ["configured", "discovered"]
    assert found[1].group == "registry"


def test_get_backends_public_url(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backends at or below the public URL of proTES are never used."""
    discovering.add("self", "https://protes.example.org/tes/ga4gh/tes/v1")
    discovering.add("b", "https://tes1.example.org")
    monkeypatch.setattr(
        get_config().server, "public_url", "https://protes.example.org/tes"
    )
    assert [backend.url for backend in backends.get_backends()] == [
        STATIC,
        "https://tes1.example.org",
    ]
    assert backends.get_backend_list(public_url=STATIC).public_url == STATIC


def test_get_backend(discovering: FakeRegistry) -> None:
    """Backends are looked up by normalized URL, with their entries."""
    discovering.add("a", "https://tes1.example.org")
    backend, entry = backends.get_backend("https://TES1.example.org/")
    assert backend.url == "https://tes1.example.org"
    assert entry.id == "registry"
    backend, entry = backends.get_backend(STATIC)
    assert backend.source == "configured"
    assert entry.url == STATIC


def test_get_backend_refresh(discovering: FakeRegistry, clock: Clock) -> None:
    """Backends that are not in the cache are looked up in the registry again.

    E.g., backends that another process discovered already; at most every
    `MIN_REFRESH_INTERVAL` seconds.
    """
    assert len(backends.get_backends()) == 1
    discovering.add("a", "https://tes1.example.org")
    clock.now += registry.MIN_REFRESH_INTERVAL
    assert backends.get_backend("https://tes1.example.org")[0].url == (
        "https://tes1.example.org"
    )
    discovering.add("b", "https://tes2.example.org")
    with pytest.raises(BackendNotConfigured, match="tes2"):
        backends.get_backend("https://tes2.example.org")
    assert len(discovering.reads) == 4


def test_get_backend_no_registry(app: FlaskApp, fake_registry: FakeRegistry) -> None:
    """Without registries, unknown backends are not looked up."""
    with app.app.app_context(), pytest.raises(BackendNotConfigured):
        backends.get_backend("https://other.example.org")
    assert not fake_registry.reads


def test_service_info_fields(discovering: FakeRegistry) -> None:
    """The service info lists the configured and discovered backends."""
    discovering.add("a", "https://tes1.example.org")
    fields = backends.service_info_fields()
    assert fields.pop("gateway")["plugins_url"] == "/ga4gh/tes/v1/plugins"
    assert fields == {
        "backends": [
            {
                "id": STATIC,
                "url": STATIC,
                "source": "configured",
                "group": None,
                "labels": {},
            },
            {
                "id": "https://tes1.example.org",
                "url": "https://tes1.example.org",
                "source": "discovered",
                "group": "registry",
                "labels": {},
            },
        ]
    }


def test_service_info(client: FlaskClient) -> None:
    """`GET /service-info` lists the backends of the test overlay."""
    response = client.get(f"{BASE_PATH}/service-info")
    assert response.status_code == 200
    assert [backend["url"] for backend in response.get_json()["backends"]] == [
        "https://tes.example.org"
    ]


def test_client_discovered(discovering: FakeRegistry) -> None:
    """Requests to discovered backends don't follow redirects.

    With `auth: none`, they get no credentials.
    """
    discovering.add("a", "https://tes1.example.org")
    kwargs = backends.create_client(host="https://tes1.example.org")._request_params()
    assert kwargs["allow_redirects"] is False
    assert "Authorization" not in kwargs["headers"]


class Listener:
    """Local TCP server that counts and closes the connections it accepts.

    Attributes:
        port: Port.
        accepted: Number of connections accepted.
    """

    def __init__(self) -> None:
        """Listen on a free port of `127.0.0.1`."""
        self._sock = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._sock.getsockname()[1]
        self.accepted = 0
        self.connected = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        """Accept and close connections until closed."""
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.accepted += 1
            self.connected.set()
            conn.close()

    def close(self) -> None:
        """Stop listening."""
        self._sock.close()


@pytest.fixture
def rebinding(
    discovering: FakeRegistry, loopback: None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, Listener]]:
    """A discovered backend whose host gets a private address after discovery.

    Discovery saw a public address (the fake DNS of the registry); connections
    resolve the host to `127.0.0.1`, where a local server listens.

    Yields:
        URL of the backend, and the local server.
    """
    listener = Listener()
    resolve = socket.getaddrinfo

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        """Resolve the backend's host to the local address."""
        if host == "tes1.example.org":
            host = "127.0.0.1"
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    url = f"https://tes1.example.org:{listener.port}"
    discovering.add("a", url)
    try:
        yield url, listener
    finally:
        listener.close()


def test_client_connection_checked(rebinding: tuple[str, Listener]) -> None:
    """Each connection to a discovered backend is checked (DNS rebinding).

    Neither the task nor the request for it reaches a private address: no
    connection is made.
    """
    url, listener = rebinding
    client = backends.create_client(host=url)
    assert client.guarded is not None
    assert not client.guarded.allow_private_addresses
    assert not client.guarded.allow_http
    assert client.guarded.max_response_size == backends.MAX_RESPONSE_SIZE
    assert client.guarded.timeout == client.timeout
    with pytest.raises(BackendAddressNotAllowed, match="not public"):
        client.get_task("r1")
    request = OutboundRequest(
        backend=client.backend, method="POST", path="/ga4gh/tes/v1/tasks"
    )
    with pytest.raises(BackendAddressNotAllowed) as info:
        client.forward(request, tes.Task(executors=[]))
    assert backends.not_sent(info.value)
    assert backends.forward_reason(info.value) == "address_not_allowed"
    assert listener.accepted == 0


def test_client_private_allowed(rebinding: tuple[str, Listener]) -> None:
    """Discovered backends may have private addresses if allowed.

    The local server isn't a TES backend (no TLS), so the request fails
    after connecting.
    """
    url, listener = rebinding
    get_custom_config(CustomConfig).tes.backends = backend_list(
        allow_private_addresses=True
    )
    client = backends.create_client(host=url)
    assert client.guarded is not None
    assert client.guarded.allow_private_addresses
    # the TLS handshake fails, a TLS error of unknown outcome
    with pytest.raises(requests.ConnectionError):
        client.get_task("r1")
    assert listener.connected.wait(5)
    assert listener.accepted == 1


@pytest.mark.parametrize(
    "error, expected",
    [
        (HostNotResolved("x not resolved"), BackendAddressNotAllowed),
        (http.RequestNotSent("x not reached"), BackendRequestNotSent),
        (http.InvalidRequest("x", reason="invalid_header"), requests.ConnectionError),
        (http.InvalidRequestURL("x"), requests.ConnectionError),
        (http.RequestMaybeSent("x", reason="read_timeout"), requests.ReadTimeout),
        (http.RequestMaybeSent("x lost"), requests.ConnectionError),
    ],
)
def test_client_guarded_errors(
    discovering: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: type[Exception],
) -> None:
    """Errors of the guarded client are those of `requests`, as classified."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Fail as the guarded client does."""
        raise error

    assert client.guarded is not None
    monkeypatch.setattr(client.guarded, "request", fail)
    with pytest.raises(expected) as info:
        client.get_task("r1")
    assert isinstance(info.value, requests.RequestException)
    assert backends.not_sent(info.value) is issubclass(expected, BackendRequestNotSent)
    assert backends.forward_reason(info.value) in {
        "address_not_allowed",
        "connect_error",
    }


@pytest.mark.parametrize(
    "cause, sent",
    [(ssl.SSLCertVerificationError("bad certificate"), False), (ssl.SSLError(), None)],
)
def test_client_guarded_tls_error(
    discovering: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
    cause: Exception,
    sent: bool | None,
) -> None:
    """Only refused certificates count as TLS errors before sending.

    Other TLS errors may come after the request was sent (outcome unknown).
    """
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Fail with a TLS error, as the guarded client does."""
        raise http.RequestNotSent("TLS error", reason="tls_error") from cause

    assert client.guarded is not None
    monkeypatch.setattr(client.guarded, "request", fail)
    request = OutboundRequest(
        backend=client.backend, method="POST", path="/ga4gh/tes/v1/tasks"
    )
    if sent is False:
        with pytest.raises(BackendRequestNotSent):
            client.forward(request, tes.Task(executors=[]))
    else:
        with pytest.raises(BackendOutcomeUnknown):
            client.forward(request, tes.Task(executors=[]))


@pytest.mark.parametrize(
    "cause, reason",
    [
        (ConnectTimeoutError("timed out"), "connect_timeout"),
        (NewConnectionError(None, "refused"), "connect_error"),  # type: ignore[arg-type]
    ],
)
def test_client_guarded_connect_errors(
    discovering: FakeRegistry,
    monkeypatch: pytest.MonkeyPatch,
    cause: Exception,
    reason: str,
) -> None:
    """Connect timeouts of discovered backends are reported as such."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")

    def fail(*args: Any, **kwargs: Any) -> Any:
        """Fail as the guarded client does when connecting fails."""
        raise http.RequestNotSent("not reached") from cause

    assert client.guarded is not None
    monkeypatch.setattr(client.guarded, "request", fail)
    with pytest.raises(BackendRequestNotSent) as info:
        client.get_task("r1")
    assert backends.not_sent(info.value)
    assert backends.forward_reason(info.value) == reason


def test_client_guarded_arguments(discovering: FakeRegistry) -> None:
    """Guarded requests take the arguments that the guarded client supports."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")
    with pytest.raises(TypeError, match="stream"):
        client._request("GET", "https://tes1.example.org", (1, 1), stream=True)


@pytest.mark.parametrize(
    "status, reason, encoding",
    [(200, "OK", "utf-8"), (299, "", None)],
)
def test_as_response(status: int, reason: str, encoding: str | None) -> None:
    """Responses of the guarded client keep status, reason and media type."""
    headers = {"content-type": "application/json; charset=utf-8"} if encoding else {}
    response = backends.as_response(
        http.HttpResponse(status=status, headers=headers, content=b"{}"), "https://x"
    )
    assert (response.status_code, response.reason) == (status, reason)
    assert response.encoding == encoding
    assert response.json() == {}


def test_client_configured_not_checked(discovering: FakeRegistry) -> None:
    """Configured backends are not guarded (e.g., internal ones)."""
    client = backends.create_client(host=STATIC)
    assert client.guarded is None
    assert "allow_redirects" not in client._request_params()


def test_client_discovered_token(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovered backends with `auth: client_credentials` get proTES' token."""
    tes = get_custom_config(CustomConfig).tes
    tes.backends = backend_list(auth="client_credentials")
    tes.client_credentials = ClientCredentials(
        token_endpoint="https://idp.example.org/token"  # noqa: S106 (a URL)
    )
    discovering.add("a", "https://tes1.example.org")
    monkeypatch.setattr(
        backends.token_provider, "get_token", lambda config: "token-of-protes"
    )
    client = backends.create_client(host="https://tes1.example.org")
    assert client._request_params()["headers"]["Authorization"] == (
        "Bearer token-of-protes"
    )


def test_task_relayed_to_discovered_backend(
    client: FlaskClient,
    backend: type[FakeBackend],
    fake_registry: FakeRegistry,
    clock: Clock,
    app: FlaskApp,
) -> None:
    """Tasks are relayed to discovered backends, with no config change.

    Within one refresh interval of their verification; never to unverified
    services or those with internal addresses.
    """
    with app.app.app_context():
        get_custom_config(CustomConfig).tes.backends = BackendListConfig.model_validate(
            [{"type": "ga4gh-service-registry", "url": REGISTRY, "auth": "none"}]
        )
    fake_registry.add("u", "https://unverified.example.org", verified=False)
    fake_registry.add("i", "https://internal.example.org")
    fake_registry.hosts["internal.example.org"] = ["10.0.0.1"]
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 503
    fake_registry.add("a", "https://tes1.example.org/ga4gh/tes/v1")
    clock.now += 300
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert [url for url, _ in relayed(backend)] == ["https://tes1.example.org"]
    (discovered,) = backend.clients
    assert discovered.source == "discovered"
    assert discovered.group == REGISTRY


def test_client_guarded_response(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Responses of the guarded client are used as those of `requests`."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")
    sent: list[tuple[str, str, dict[str, Any]]] = []

    def answer(method: str, url: str, **kwargs: Any) -> http.HttpResponse:
        """Record the request, and answer with a task."""
        sent.append((method, url, kwargs))
        return http.HttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            content=b'{"id": "r1", "state": "RUNNING"}',
        )

    assert client.guarded is not None
    monkeypatch.setattr(client.guarded, "request", answer)
    assert client.get_task("r1").state == "RUNNING"
    request = OutboundRequest(
        backend=client.backend,
        method="POST",
        path="/ga4gh/tes/v1/tasks",
        query={"x": ["1"]},
    )
    task_id, response = client.forward(request, tes.Task(executors=[]))
    assert task_id == "r1"
    assert response.headers["Content-Type"] == "application/json"
    (_, get_url, _), (method, post_url, kwargs) = sent
    assert get_url == "https://tes1.example.org/ga4gh/tes/v1/tasks/r1"
    assert (method, post_url) == ("POST", "https://tes1.example.org/ga4gh/tes/v1/tasks")
    assert kwargs["params"] == {"x": ["1"]}
    assert kwargs["data"] == tes.Task(executors=[]).as_json()
    assert "allow_redirects" not in kwargs
