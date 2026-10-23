"""Tests of the discovery of TES backends in a service registry."""

from collections.abc import Iterator
import ipaddress
import json
import logging
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

from connexion import FlaskApp
from flask.testing import FlaskClient
import pydantic
import pytest
import requests

from pro_tes.config_models import (
    ClientCredentials,
    DiscoveredBackend,
    Discovery,
    Tes,
    TesBackend,
)
from pro_tes.exceptions import (
    BackendAddressNotAllowed,
    BackendNotConfigured,
    HostNotResolved,
    RegistryError,
)
from pro_tes.ga4gh.tes import backends, discovery
from pro_tes.utils.misc import get_custom_config
from tests.conftest import mock_get
from tests.ga4gh.tes.conftest import BASE_PATH, TASK, FakeBackend, relayed

REGISTRY = "https://registry.example.org/ga4gh/registry/v1"
TES_TYPE = {"group": "org.ga4gh", "artifact": "tes", "version": "1.1.0"}
# Public addresses of the fake DNS (documentation addresses are not global)
PUBLIC_IPV4 = "93.184.215.14"
PUBLIC_IPV6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"


def service(
    service_id: str,
    url: str,
    api: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Get a registry entry of a service.

    Args:
        service_id: Identifier of the service.
        url: URL of the service.
        api: API type; by default, TES 1.1.0.
        **fields: Other fields, e.g., `environment`.

    Returns:
        The service, as listed by `GET /services`.
    """
    return {
        "id": service_id,
        "name": service_id,
        "url": url,
        "type": api or TES_TYPE,
        "organization": {"name": "Example", "url": "https://example.org"},
        "version": "1.0.0",
        **fields,
    }


def verification(entry: dict[str, Any]) -> dict[str, Any]:
    """Get the verification of a registry entry, as it is registered.

    Args:
        entry: Service, as listed by `GET /services`.

    Returns:
        The verification, as listed by `GET /verifications`.
    """
    return {
        "service_id": entry["id"],
        "url": entry["url"],
        "type": entry["type"],
        "verified_at": "2026-10-20T12:34:56.789Z",
    }


class FakeResponse:
    """Streamed response of the fake registry.

    Attributes:
        status_code: HTTP status code.
        content: Body.
        error: Exception raised while the body is read, if any.
        closed: Whether the response was closed.
    """

    def __init__(
        self,
        content: bytes,
        status_code: int = 200,
        error: Exception | None = None,
    ) -> None:
        """Create a response."""
        self.content = content
        self.status_code = status_code
        self.error = error
        self.closed = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        """Iterate over the body in chunks.

        Raises:
            Exception: `error`, if set, after the first chunk.
        """
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]
            if self.error is not None:
                raise self.error

    def close(self) -> None:
        """Close the response."""
        self.closed = True


class FakeRegistry:
    """Fake service registry, with fake DNS for the hosts of its services.

    Attributes:
        services: Services listed (`GET /services`).
        verifications: Verifications listed (`GET /verifications`).
        hosts: Addresses of host names.
        down: Whether the registry is unreachable.
        requests: Requested URLs, with the keyword arguments of the requests.
        responses: Responses served, by URL, instead of the lists.
    """

    def __init__(self) -> None:
        """Create a registry without services."""
        self.services: Any = []
        self.verifications: Any = []
        self.hosts: dict[str, list[str]] = {}
        self.down = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, FakeResponse] = {}

    def add(self, entry: dict[str, Any], verified: bool = True) -> None:
        """Register a service, with a public address for its host.

        Args:
            entry: Service.
            verified: Whether the service is verified.
        """
        self.services.append(entry)
        if verified:
            self.verifications.append(verification(entry))
        host = urlsplit(entry["url"]).hostname
        if host is not None:
            self.hosts.setdefault(host, [PUBLIC_IPV4])

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Serve the services or verifications, or the identity provider.

        Raises:
            requests.ConnectionError: The registry is down.
        """
        self.requests.append((url, kwargs))
        if self.down:
            raise requests.ConnectionError("down")
        if url in self.responses:
            return self.responses[url]
        if url == f"{REGISTRY}/services":
            return FakeResponse(json.dumps(self.services).encode())
        if url == f"{REGISTRY}/verifications":
            return FakeResponse(json.dumps(self.verifications).encode())
        if not url.startswith(REGISTRY):
            # the identity provider of the app tests
            return mock_get(url, **kwargs)  # type: ignore[return-value]
        return FakeResponse(b"{}", status_code=404)

    def getaddrinfo(self, host: str, port: int, **kwargs: Any) -> list[Any]:
        """Resolve a host name.

        Raises:
            socket.gaierror: The host is unknown.
        """
        if host not in self.hosts:
            raise socket.gaierror(f"Unknown host: {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in self.hosts[host]
        ]


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
def registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    """Fake registry and DNS, with a fake clock of the cache."""
    fake = FakeRegistry()
    monkeypatch.setattr(discovery.requests, "get", fake.get)
    monkeypatch.setattr(discovery, "getaddrinfo", fake.getaddrinfo)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Fake clock of the cache of discovered backends."""
    fake = Clock()
    monkeypatch.setattr(discovery.time, "monotonic", fake)
    return fake


def conf(**kwargs: Any) -> Discovery:
    """Get a discovery configuration with the fake registry.

    Args:
        **kwargs: Other settings.

    Returns:
        Discovery configuration.
    """
    return Discovery(registry_url=REGISTRY, **kwargs)


def urls(found: list[DiscoveredBackend] | list[TesBackend]) -> list[str]:
    """Get the URLs of backends.

    Args:
        found: Backends.

    Returns:
        Their URLs.
    """
    return [backend.url for backend in found]


def select(registry: FakeRegistry, **kwargs: Any) -> discovery.Selection:
    """Select backends from the services of the fake registry.

    Args:
        registry: Fake registry.
        **kwargs: Discovery settings.

    Returns:
        Selected backends.
    """
    return discovery.select_backends(
        services=registry.services,
        verifications=registry.verifications,
        conf=conf(**kwargs),
    )


# Addresses


@pytest.mark.parametrize(
    "address, public",
    [
        (PUBLIC_IPV4, True),
        (PUBLIC_IPV6, True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),  # noqa: S104 (an address to check)
        ("224.0.0.1", False),
        ("192.0.2.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        (f"::ffff:{PUBLIC_IPV4}", True),
        ("64:ff9b::7f00:1", False),
        ("64:ff9b::5db8:d70e", True),
        ("::7f00:1", False),
        ("::5db8:d70e", True),
        ("ff02::1", False),
    ],
)
def test_public_address(address: str, public: bool) -> None:
    """Only globally reachable unicast addresses are public.

    Also for IPv4 addresses embedded in IPv6 addresses.
    """
    assert discovery.is_public_address(ipaddress.ip_address(address)) is public


def test_public_host(registry: FakeRegistry) -> None:
    """Hosts with public addresses only are allowed.

    IP addresses are not resolved.
    """
    registry.hosts["tes.example.org"] = [PUBLIC_IPV4, PUBLIC_IPV6]
    discovery.check_public_host("https://tes.example.org:8443/api")
    discovery.check_public_host(f"https://{PUBLIC_IPV4}")
    discovery.check_public_host(f"https://[{PUBLIC_IPV6}]")


@pytest.mark.parametrize(
    "url, addresses, match",
    [
        ("https://tes.example.org", [PUBLIC_IPV4, "10.0.0.1"], "not public: 10.0.0.1"),
        ("https://tes.example.org", [], "has no address"),
        ("https://tes.example.org", ["not-an-address"], "not public"),
        ("https://unknown.example.org", None, "not resolved: gaierror"),
        ("https://127.0.0.1", None, "not public: 127.0.0.1"),
        ("https://[::1]", None, r"not public: ::1"),
        ("https://[fe80::1%25eth0]", None, "zone identifier"),
    ],
)
def test_host_not_public(
    registry: FakeRegistry, url: str, addresses: list[str] | None, match: str
) -> None:
    """Hosts with an address that is not public, or none, are rejected."""
    if addresses is not None:
        registry.hosts["tes.example.org"] = addresses
    with pytest.raises(BackendAddressNotAllowed, match=match):
        discovery.check_public_host(url)


def test_host_not_encodable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosts that can't be resolved, e.g., as they can't be encoded, are rejected."""

    def fail(*args: Any, **kwargs: Any) -> list[Any]:
        """Fail like `getaddrinfo()` for hosts that IDNA can't encode."""
        raise UnicodeError("label too long")

    monkeypatch.setattr(discovery, "getaddrinfo", fail)
    with pytest.raises(BackendAddressNotAllowed, match="UnicodeError"):
        discovery.check_public_host("https://tes.example.org")


def test_address_not_allowed_is_connection_error() -> None:
    """Rejected addresses are handled as unreachable backends are."""
    assert issubclass(BackendAddressNotAllowed, requests.ConnectionError)


# Registry responses


def test_fetch_json(registry: FakeRegistry) -> None:
    """Registry responses are read without token, and redirects not followed."""
    registry.verifications = [{"service_id": "a"}]
    assert discovery.fetch_json(f"{REGISTRY}/verifications", timeout=3) == [
        {"service_id": "a"}
    ]
    url, kwargs = registry.requests[0]
    assert url == f"{REGISTRY}/verifications"
    assert kwargs["headers"] == {"Accept": "application/json"}
    assert kwargs["timeout"] == 3
    assert kwargs["allow_redirects"] is False
    assert "auth" not in kwargs


@pytest.mark.parametrize(
    "response, match",
    [
        (FakeResponse(b"[]", status_code=500), "status 500"),
        (FakeResponse(b"", status_code=302), "status 302"),
        (FakeResponse(b"[" * 200000 + b"]" * 200000), "invalid JSON"),
        (FakeResponse(b"not json"), "invalid JSON"),
        (
            FakeResponse(b"[" + b" " * 70000 + b"]", error=requests.ConnectionError()),
            "ConnectionError",
        ),
        (FakeResponse(b" " * (discovery.MAX_RESPONSE_SIZE + 1)), "larger than"),
    ],
)
def test_fetch_json_invalid(
    registry: FakeRegistry, response: FakeResponse, match: str
) -> None:
    """Failed or invalid responses are errors; responses are closed."""
    registry.responses[f"{REGISTRY}/services"] = response
    with pytest.raises(RegistryError, match=match):
        discovery.fetch_json(f"{REGISTRY}/services", timeout=3)
    assert response.closed


def test_fetch_json_unreachable(registry: FakeRegistry) -> None:
    """Unreachable registries are errors."""
    registry.down = True
    with pytest.raises(RegistryError, match="unavailable: ConnectionError"):
        discovery.fetch_json(f"{REGISTRY}/services", timeout=3)


# Selection


def test_select_verified(registry: FakeRegistry) -> None:
    """Verified TES services are used, normalized, without the API path.

    In the order of the registry, once per URL, with the configured `auth`.
    """
    registry.add(service("a", "https://TES1.example.org/ga4gh/tes/v1/"))
    registry.add(service("b", "https://tes2.example.org:8443/api"))
    registry.add(service("c", "https://tes1.example.org/ga4gh/tes/v1"))
    selection = select(registry, auth="none")
    assert urls(selection.backends) == [
        "https://tes1.example.org",
        "https://tes2.example.org:8443/api",
    ]
    assert [backend.service_id for backend in selection.backends] == ["a", "b"]
    assert {backend.auth for backend in selection.backends} == {"none"}
    assert not selection.rejected


def test_select_unverified(registry: FakeRegistry) -> None:
    """Unverified services are never used, nor are other services' URLs."""
    registry.add(service("a", "https://tes1.example.org"), verified=False)
    registry.add(service("b", "https://tes2.example.org"))
    registry.verifications[0]["service_id"] = "a"
    assert not select(registry).backends


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://tes1.example.org/"},
        {"url": None},
        {"type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"}},
        {"type": None},
    ],
)
def test_select_verification_differs(
    registry: FakeRegistry, change: dict[str, Any]
) -> None:
    """Services are used only with the URL and API type that were verified."""
    registry.add(service("a", "https://tes1.example.org"))
    registry.verifications[0].update(change)
    selection = select(registry)
    assert not selection.backends
    assert selection.rejected == ["'a': URL or API type differ from the verified ones"]


def test_select_type(registry: FakeRegistry) -> None:
    """Only TES services of the accepted versions are used.

    Group and artifact are compared case-insensitively; versions as
    prefixes.
    """
    apis: list[dict[str, Any]] = [
        {"group": "ORG.GA4GH", "artifact": "TES", "version": "1.1"},
        {"group": "org.ga4gh", "artifact": "tes", "version": "1.0.0"},
        {"group": "org.ga4gh", "artifact": "tes", "version": "1.10.0"},
        {"group": "org.ga4gh", "artifact": "tes", "version": "2.0.0"},
        {"group": "org.ga4gh", "artifact": "tes", "version": None},
        {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"},
        {"group": "org.example", "artifact": "tes", "version": "1.1.0"},
    ]
    for number, api in enumerate(apis):
        registry.add(service(f"s{number}", f"https://tes{number}.example.org", api))
    assert urls(select(registry).backends) == [
        "https://tes0.example.org",
        "https://tes1.example.org",
    ]
    assert urls(select(registry, versions=["1.10", "2"]).backends) == [
        "https://tes2.example.org",
        "https://tes3.example.org",
    ]


def test_select_environment(registry: FakeRegistry) -> None:
    """Only services of the accepted environments are used, if set."""
    registry.add(service("a", "https://tes1.example.org", environment="prod"))
    registry.add(service("b", "https://tes2.example.org", environment="test"))
    registry.add(service("c", "https://tes3.example.org"))
    assert len(select(registry).backends) == 3
    assert urls(select(registry, environments=["prod"]).backends) == [
        "https://tes1.example.org"
    ]


@pytest.mark.parametrize(
    "url, match",
    [
        ("http://tes.example.org", "must use one of these schemes: 'https'"),
        ("https://user:secret@tes.example.org", "must not contain user info"),
        ("https://tes.example.org:0", "need a valid port"),
        ("https://tes .example.org", "whitespace"),
        ("ftp://tes.example.org", "schemes"),
    ],
)
def test_select_invalid_url(registry: FakeRegistry, url: str, match: str) -> None:
    """Services with URLs other than valid `https` URLs are rejected."""
    registry.add(service("a", url))
    selection = select(registry)
    assert not selection.backends
    assert len(selection.rejected) == 1
    assert selection.rejected[0].startswith("'a': TES URLs")
    assert match in selection.rejected[0]


def test_select_private_address(registry: FakeRegistry) -> None:
    """Services with hosts that are not public are rejected, unless allowed."""
    registry.add(service("a", "https://tes.internal.example.org"))
    registry.add(service("b", "https://10.0.0.1"))
    registry.add(service("c", "https://localhost:8443"))
    registry.hosts["tes.internal.example.org"] = ["10.1.2.3"]
    registry.hosts["localhost"] = ["127.0.0.1", "::1"]
    selection = select(registry)
    assert not selection.backends
    assert len(selection.rejected) == 3
    assert "not public: 10.1.2.3" in selection.rejected[0]
    assert urls(select(registry, allow_private_addresses=True).backends) == [
        "https://tes.internal.example.org",
        "https://10.0.0.1",
        "https://localhost:8443",
    ]


def test_select_malformed_entries(registry: FakeRegistry) -> None:
    """Malformed services are skipped."""
    registry.add(service("a", "https://tes1.example.org"))
    registry.services[:0] = [
        "not an object",
        {"id": 1, "type": TES_TYPE},
        {"id": "x", "type": "tes"},
    ]
    assert urls(select(registry).backends) == ["https://tes1.example.org"]


@pytest.mark.parametrize(
    "services, verifications, match",
    [
        ({}, [], "Services are not a list"),
        ([], {}, "Verifications are not a list"),
        ([], ["a"], "without service identifier"),
        ([], [{"service_id": 1}], "without service identifier"),
    ],
)
def test_select_invalid_responses(
    services: Any, verifications: Any, match: str
) -> None:
    """Invalid responses are errors."""
    with pytest.raises(RegistryError, match=match):
        discovery.select_backends(
            services=services, verifications=verifications, conf=conf()
        )


# Cache


def test_discovery_off(registry: FakeRegistry) -> None:
    """Without registry URL, the registry is not read."""
    assert discovery.discovery.get_backends(Discovery()) == []
    assert not registry.requests


def test_cache(registry: FakeRegistry, clock: Clock) -> None:
    """The registry is read again after the refresh interval only.

    New verified services are used within one refresh interval.
    """
    registry.add(service("a", "https://tes1.example.org"))
    config = conf(refresh_interval=60)
    cache = discovery.BackendDiscovery()
    assert urls(cache.get_backends(config)) == ["https://tes1.example.org"]
    assert [url for url, _ in registry.requests] == [
        f"{REGISTRY}/verifications",
        f"{REGISTRY}/services",
    ]
    registry.add(service("b", "https://tes2.example.org"))
    clock.now += 59
    assert urls(cache.get_backends(config)) == ["https://tes1.example.org"]
    assert len(registry.requests) == 2
    clock.now += 1
    assert urls(cache.get_backends(config)) == [
        "https://tes1.example.org",
        "https://tes2.example.org",
    ]
    assert len(registry.requests) == 4


def test_cache_last_good(
    registry: FakeRegistry, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """The last good list is kept while the registry is unavailable or invalid.

    Revoked verifications take effect once the registry is read again.
    """
    registry.add(service("a", "https://tes1.example.org"))
    config = conf(refresh_interval=60)
    cache = discovery.BackendDiscovery()
    assert len(cache.get_backends(config)) == 1
    registry.down = True
    clock.now += 60
    with caplog.at_level(logging.WARNING):
        assert len(cache.get_backends(config)) == 1
    assert "keeping 1 backends discovered earlier" in caplog.text
    registry.down = False
    registry.responses[f"{REGISTRY}/services"] = FakeResponse(b'{"a": 1}')
    clock.now += 60
    assert len(cache.get_backends(config)) == 1
    del registry.responses[f"{REGISTRY}/services"]
    registry.verifications.clear()
    clock.now += 60
    assert cache.get_backends(config) == []


def test_cache_registry_down_first(registry: FakeRegistry, clock: Clock) -> None:
    """Without a good list, no backends are discovered.

    Failed reads are retried after `RETRY_INTERVAL` seconds.
    """
    registry.down = True
    cache = discovery.BackendDiscovery()
    assert cache.get_backends(conf()) == []
    registry.down = False
    registry.add(service("a", "https://tes1.example.org"))
    clock.now += discovery.RETRY_INTERVAL - 1
    assert cache.get_backends(conf()) == []
    clock.now += 1
    assert len(cache.get_backends(conf())) == 1


def test_cache_unexpected_error(
    registry: FakeRegistry,
    clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected errors of reads are logged; they never fail requests."""

    def fail(*args: Any, **kwargs: Any) -> discovery.Selection:
        """Fail like a bug."""
        raise RuntimeError("bug")

    monkeypatch.setattr(discovery, "select_backends", fail)
    cache = discovery.BackendDiscovery()
    assert cache.get_backends(conf()) == []
    assert "TES backends not discovered" in caplog.text
    assert "RuntimeError: bug" in caplog.text


def test_cache_host_not_resolved(registry: FakeRegistry, clock: Clock) -> None:
    """Backends whose hosts can't be resolved for now are kept for one read.

    Only backends discovered before, with the same URL; not those with hosts
    that resolve to addresses that are not public.
    """
    registry.add(service("a", "https://tes1.example.org"))
    cache = discovery.BackendDiscovery()
    assert len(cache.get_backends(conf())) == 1
    del registry.hosts["tes1.example.org"]
    registry.add(service("b", "https://tes2.example.org"))
    del registry.hosts["tes2.example.org"]
    clock.now += 300
    assert urls(cache.get_backends(conf())) == ["https://tes1.example.org"]
    clock.now += 300
    assert cache.get_backends(conf()) == []
    registry.hosts["tes1.example.org"] = [PUBLIC_IPV4]
    clock.now += 300
    assert len(cache.get_backends(conf())) == 1
    registry.hosts["tes1.example.org"] = ["10.0.0.1"]
    clock.now += 300
    assert cache.get_backends(conf()) == []


def test_host_not_resolved_error() -> None:
    """Unresolved hosts are not allowed."""
    assert issubclass(HostNotResolved, BackendAddressNotAllowed)


def test_cache_config_changed(registry: FakeRegistry, clock: Clock) -> None:
    """The registry is read anew when the configuration changes."""
    registry.add(service("a", "https://tes1.example.org", environment="test"))
    cache = discovery.BackendDiscovery()
    assert len(cache.get_backends(conf())) == 1
    assert cache.get_backends(conf(environments=["prod"])) == []
    assert len(registry.requests) == 4


def test_cache_logged(
    registry: FakeRegistry, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """Changes of the backends, and rejected services, are logged once."""
    registry.add(service("a", "https://tes1.example.org"))
    registry.add(service("b", "http://tes2.example.org"))
    cache = discovery.BackendDiscovery()
    with caplog.at_level(logging.INFO, logger=discovery.__name__):
        cache.get_backends(conf())
        clock.now += 300
        cache.get_backends(conf())
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "Registry service not used: 'b': TES URLs must use one of these"
        " schemes: 'https'.",
        f"TES backends discovered at registry {REGISTRY}: https://tes1.example.org",
    ]
    caplog.clear()
    registry.services.clear()
    clock.now += 300
    with caplog.at_level(logging.INFO, logger=discovery.__name__):
        cache.get_backends(conf())
    assert caplog.messages == [f"TES backends discovered at registry {REGISTRY}: none"]


def test_cache_busy(registry: FakeRegistry, clock: Clock) -> None:
    """While the registry is read, other threads get the cached backends.

    Before the first read, they wait for it.
    """
    registry.add(service("a", "https://tes1.example.org"))
    cache = discovery.BackendDiscovery()
    config = conf()
    assert len(cache.get_backends(config)) == 1
    clock.now += 300
    registry.services.clear()
    with cache._lock:
        assert len(cache.get_backends(config)) == 1
    assert len(registry.requests) == 2
    other = discovery.BackendDiscovery()
    results: list[list[DiscoveredBackend]] = []
    with other._lock:
        thread = threading.Thread(
            target=lambda: results.append(other.get_backends(config))
        )
        thread.start()
        thread.join(timeout=0.2)
        assert thread.is_alive()
    thread.join()
    assert results == [[]]


def test_cache_reset_lock() -> None:
    """The lock can be replaced, e.g., after a fork."""
    cache = discovery.BackendDiscovery()
    cache._lock.acquire()
    cache._reset_lock()
    assert not cache._lock.locked()


# Backends of the app


@pytest.fixture
def discovering(
    app: FlaskApp, registry: FakeRegistry, clock: Clock
) -> Iterator[FakeRegistry]:
    """App context with a static backend and discovery without credentials."""
    with app.app.app_context():
        tes = get_custom_config().tes
        tes.service_list = [TesBackend(url="https://static.example.org/", auth="none")]
        tes.discovery = conf(auth="none")
        yield registry


def test_get_backends(discovering: FakeRegistry) -> None:
    """Configured backends come first, and take precedence over discovered ones."""
    discovering.add(service("a", "https://STATIC.example.org/ga4gh/tes/v1"))
    discovering.add(service("b", "https://tes1.example.org"))
    found = discovery.get_backends()
    assert urls(found) == ["https://static.example.org/", "https://tes1.example.org"]
    assert not isinstance(found[0], DiscoveredBackend)
    assert backends.get_backend("https://tes1.example.org/").auth == "none"


def test_get_backends_invalid_static(discovering: FakeRegistry) -> None:
    """Configured backends with URLs that can't be normalized are compared as is."""
    backend = TesBackend.model_construct(
        url="https://static.example.org:x/", auth="none"
    )
    get_custom_config().tes.service_list = [backend]
    discovering.add(service("a", "https://tes1.example.org"))
    assert urls(discovery.get_backends()) == [
        "https://static.example.org:x/",
        "https://tes1.example.org",
    ]


def test_service_info_fields(discovering: FakeRegistry) -> None:
    """The service info lists the configured and discovered backends."""
    discovering.add(service("a", "https://tes1.example.org"))
    assert discovery.service_info_fields() == {
        "backends": ["https://static.example.org/", "https://tes1.example.org"]
    }


def test_service_info(client: FlaskClient) -> None:
    """`GET /service-info` lists the backends of the test overlay."""
    response = client.get(f"{BASE_PATH}/service-info")
    assert response.status_code == 200
    assert response.get_json()["backends"] == ["https://tes.example.org/"]


def test_client_discovered(discovering: FakeRegistry) -> None:
    """Requests to discovered backends don't follow redirects.

    With `auth: none`, they get no credentials.
    """
    discovering.add(service("a", "https://tes1.example.org"))
    kwargs = backends.create_client(host="https://tes1.example.org")._request_params()
    assert kwargs["allow_redirects"] is False
    assert "Authorization" not in kwargs["headers"]


def test_client_address_checked(discovering: FakeRegistry) -> None:
    """Before each request to a discovered backend, its host is checked."""
    discovering.add(service("a", "https://tes1.example.org"))
    client = backends.create_client(host="https://tes1.example.org")
    discovering.hosts["tes1.example.org"] = ["127.0.0.1"]
    with pytest.raises(BackendAddressNotAllowed, match="not public"):
        client._request_params()


def test_client_private_allowed(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosts of discovered backends aren't checked if private ones are allowed."""
    monkeypatch.setattr(
        get_custom_config().tes,
        "discovery",
        conf(auth="none", allow_private_addresses=True),
    )
    discovering.add(service("a", "https://tes1.example.org"))
    discovering.hosts["tes1.example.org"] = ["10.0.0.1"]
    client = backends.create_client(host="https://tes1.example.org")
    assert client._request_params()["allow_redirects"] is False


def test_get_backend_refresh(discovering: FakeRegistry, clock: Clock) -> None:
    """Backends that are not in the cache are looked up in the registry again.

    E.g., backends that another process discovered already; at most every
    `MIN_REFRESH_INTERVAL` seconds.
    """
    assert len(discovery.get_backends()) == 1
    discovering.add(service("a", "https://tes1.example.org"))
    clock.now += discovery.MIN_REFRESH_INTERVAL
    assert backends.get_backend("https://TES1.example.org/").url == (
        "https://tes1.example.org"
    )
    discovering.add(service("b", "https://tes2.example.org"))
    with pytest.raises(BackendNotConfigured):
        backends.get_backend("https://tes2.example.org")
    assert len(discovering.requests) == 4


def test_get_backend_no_discovery(app: FlaskApp, registry: FakeRegistry) -> None:
    """Without discovery, unknown backends are not looked up."""
    with app.app.app_context(), pytest.raises(BackendNotConfigured):
        backends.get_backend("https://other.example.org")
    assert not registry.requests


def test_client_static_not_checked(discovering: FakeRegistry) -> None:
    """Hosts of configured backends are not checked (e.g., internal ones)."""
    client = backends.create_client(host="https://static.example.org")
    assert client._request_params()["timeout"] == backends.TIMEOUT
    assert not discovering.requests or all(
        "static" not in url for url, _ in discovering.requests
    )


def test_client_discovered_token(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovered backends with `auth: client_credentials` get proTES' token."""
    tes = get_custom_config().tes
    tes.discovery = conf()
    tes.client_credentials = ClientCredentials(
        token_endpoint="https://idp.example.org/token"  # noqa: S106 (a URL)
    )
    discovering.add(service("a", "https://tes1.example.org"))
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
    registry: FakeRegistry,
    clock: Clock,
    app: FlaskApp,
) -> None:
    """Tasks are relayed to discovered backends, with no config change.

    Within one refresh interval of their verification; never to unverified
    services or those with internal addresses.
    """
    with app.app.app_context():
        tes = get_custom_config().tes
        tes.service_list = []
        tes.discovery = conf(auth="none")
    registry.add(service("u", "https://unverified.example.org"), verified=False)
    registry.add(service("i", "https://internal.example.org"))
    registry.hosts["internal.example.org"] = ["10.0.0.1"]
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    registry.add(service("a", "https://tes1.example.org/ga4gh/tes/v1"))
    clock.now += 300
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert [url for url, _ in relayed(backend)] == ["https://tes1.example.org"]
    assert backend.clients[-1] == DiscoveredBackend(
        url="https://tes1.example.org", auth="none", service_id="a"
    )


# Configuration


def test_discovery_config_default() -> None:
    """Discovery is off by default; discovered backends get proTES' token."""
    config = Discovery()
    assert config.registry_url is None
    assert config.auth == "client_credentials"
    assert config.allow_private_addresses is False
    assert config.environments == []


def test_discovery_config_normalized() -> None:
    """The registry URL is normalized."""
    config = Discovery(registry_url="https://Registry.example.org:443/api/v1/")
    assert config.registry_url == "https://registry.example.org/api/v1"


@pytest.mark.parametrize(
    "config, match",
    [
        ({"registry_url": "https://registry.example.org/?a=1"}, "query or fragment"),
        ({"registry_url": "https://registry.example.org/#a"}, "query or fragment"),
        ({"registry_url": "https://user@registry.example.org"}, "user info"),
        ({"registry_url": "ftp://registry.example.org"}, "schemes"),
        ({"versions": []}, "at least 1"),
        ({"versions": [""]}, "at least 1 character"),
        ({"environments": [""]}, "at least 1 character"),
        ({"auth": "basic"}, "client_credentials"),
        ({"refresh_interval": 0}, "greater than 0"),
    ],
)
def test_discovery_config_invalid(config: dict[str, Any], match: str) -> None:
    """Invalid discovery settings are rejected."""
    with pytest.raises(pydantic.ValidationError, match=match):
        Discovery(**config)


def test_tes_config_discovery() -> None:
    """The registry needs an allowed scheme; discovered tokens need an issuer."""
    with pytest.raises(pydantic.ValidationError, match="not allowed"):
        Tes(
            discovery=Discovery(registry_url="http://registry.example.org", auth="none")
        )
    with pytest.raises(pydantic.ValidationError, match="need `client_credentials"):
        Tes(discovery=Discovery(registry_url=REGISTRY))
    config = Tes(
        discovery=Discovery(registry_url="http://registry.example.org", auth="none"),
        url_schemes=["https", "http"],
    )
    assert config.discovery.registry_url == "http://registry.example.org"
    Tes(
        discovery=Discovery(registry_url=REGISTRY),
        client_credentials=ClientCredentials(issuer="https://idp.example.org/"),
    )
