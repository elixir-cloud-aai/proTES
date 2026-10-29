"""Tests of the backend list of proTES: configured and discovered TES backends.

Discovery itself (registry reads, selection, caching) is FOCA's
(:py:mod:`foca.plugins.backends`) and tested there; these tests cover how
proTES uses it, with a fake registry and fake DNS.
"""

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_config, get_custom_config
from foca.plugins.backends import BackendListConfig, registry
from foca.utils import http
from foca.utils.http import AddressNotAllowed, HostNotResolved
import pytest

from pro_tes.config_models import ClientCredentials, CustomConfig
from pro_tes.exceptions import BackendAddressNotAllowed, BackendNotConfigured
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
    monkeypatch.setattr(backends, "check_public_host", fake.check_public_host)
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
    assert backends.service_info_fields() == {
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


@pytest.mark.parametrize("addresses", [["127.0.0.1"], None])
def test_client_address_checked(
    discovering: FakeRegistry, addresses: list[str] | None
) -> None:
    """Before each request to a discovered backend, its host is checked."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")
    if addresses is None:
        del discovering.hosts["tes1.example.org"]
    else:
        discovering.hosts["tes1.example.org"] = addresses
    with pytest.raises(BackendAddressNotAllowed, match="tes1"):
        client._request_params()


def test_client_address_check_foca(
    discovering: FakeRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FOCA's check of hosts is used (hosts are not resolved in the tests)."""
    discovering.add("a", "https://tes1.example.org")
    client = backends.create_client(host="https://tes1.example.org")
    monkeypatch.setattr(backends, "check_public_host", http.check_public_host)
    with pytest.raises(BackendAddressNotAllowed, match="not resolved"):
        client._request_params()


def test_client_private_allowed(discovering: FakeRegistry) -> None:
    """Hosts of discovered backends aren't checked if private ones are allowed."""
    get_custom_config(CustomConfig).tes.backends = backend_list(
        allow_private_addresses=True
    )
    discovering.add("a", "https://tes1.example.org")
    discovering.hosts["tes1.example.org"] = ["10.0.0.1"]
    client = backends.create_client(host="https://tes1.example.org")
    assert client._request_params()["allow_redirects"] is False


def test_client_configured_not_checked(discovering: FakeRegistry) -> None:
    """Hosts of configured backends are not checked (e.g., internal ones)."""
    assert "static.example.org" not in discovering.hosts
    kwargs = backends.create_client(host=STATIC)._request_params()
    assert "allow_redirects" not in kwargs


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
    assert response.status_code == 500
    fake_registry.add("a", "https://tes1.example.org/ga4gh/tes/v1")
    clock.now += 300
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert [url for url, _ in relayed(backend)] == ["https://tes1.example.org"]
    (discovered,) = backend.clients
    assert discovered.source == "discovered"
    assert discovered.group == REGISTRY
