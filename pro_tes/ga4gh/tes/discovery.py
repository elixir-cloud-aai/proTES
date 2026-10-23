"""Discovery of TES backends in a service registry.

Besides the TES backends of `custom.tes.service_list`, proTES can forward
tasks to backends that it discovers in a service registry implementing the
GA4GH Service Registry API, e.g., the Cloud Registry
(`custom.tes.discovery`). Registry entries are registered by users, so they
aren't trusted by default; a backend is used only if:

- the registry lists it as verified (`GET /verifications`, an extension of
  the Cloud Registry), with its current identifier, URL and API type;
- it is a TES service (`org.ga4gh` `tes`) of an accepted version and
  environment (filtered here, as the registry API has no filters);
- its URL is a valid `https` URL (checked and normalized with
  :py:mod:`foca.utils.urls`), without user info;
- its host has public addresses only, unless private addresses are allowed:
  no private, loopback, link-local or other special-purpose addresses. The
  host is checked on discovery and before each request to the backend.

The registry, configured by the deployment, is the trust anchor: it is read
without access token and redirects are not followed. Each process (e.g.,
each Gunicorn or Celery worker) caches the discovered backends, refreshing
them every `refresh_interval` seconds, and keeps the last good list while
the registry is unreachable or returns invalid responses. Changes are
logged. Backends of `service_list` take precedence over discovered ones with
the same URL.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
import ipaddress
import json
import logging
import os
from socket import SOCK_STREAM, getaddrinfo
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from foca.utils.urls import InvalidURL, normalize_url
import requests

from pro_tes.config_models import DiscoveredBackend, Discovery, TesBackend
from pro_tes.utils.misc import get_custom_config

logger = logging.getLogger(__name__)

# API type of TES services (GA4GH service info `type`)
TES_GROUP = "org.ga4gh"
TES_ARTIFACT = "tes"
# API path of TES, removed from the URLs of services: service registries list
# the URLs that the paths of the API specification are relative to, whereas
# proTES (and py-tes) append the API path themselves
TES_API_PATH = "/ga4gh/tes/v1"
# Maximum size (in bytes) of responses of the registry
MAX_RESPONSE_SIZE = 16 * 1024 * 1024
# IPv4/IPv6 translation prefix (RFC 6052), whose addresses embed IPv4
# addresses
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class RegistryError(ValueError):
    """Raised when a registry response is unavailable or invalid."""


class BackendAddressNotAllowed(requests.ConnectionError):
    """Raised when the host of a discovered TES backend is not public.

    A connection error, so that proTES handles it as it handles unreachable
    backends; no request is sent.
    """


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check whether an IP address is public.

    Args:
        address: IP address.

    Returns:
        Whether the address is globally reachable (as per the IANA
        special-purpose address registries) and not a multicast address;
        IPv4 addresses embedded in IPv6 addresses (IPv4-mapped and NAT64
        ones) are checked themselves.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address in _NAT64_PREFIX:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address.is_global and not address.is_multicast


def check_public_host(url: str) -> None:
    """Check that the host of a URL has public addresses only.

    Host names are resolved; all their addresses must be public (cf.
    :py:func:`is_public_address`).

    Args:
        url: URL.

    Raises:
        BackendAddressNotAllowed: The host has no public address, or an
            address that is not public, or can't be resolved.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if "%" in host:
        raise BackendAddressNotAllowed(f"Host of {url} has a zone identifier")
    try:
        addresses: Iterable[str] = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            infos = getaddrinfo(host, parts.port or 443, type=SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise BackendAddressNotAllowed(
                f"Host of {url} not resolved: {type(exc).__name__}"
            ) from exc
        addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise BackendAddressNotAllowed(f"Host of {url} has no address")
    for address in addresses:
        try:
            public = is_public_address(ipaddress.ip_address(address))
        except ValueError:
            public = False
        if not public:
            raise BackendAddressNotAllowed(
                f"Host of {url} has an address that is not public: {address}"
            )


def fetch_json(url: str, timeout: int) -> Any:
    """Fetch a JSON document from the registry, without access token.

    Args:
        url: URL of the document.
        timeout: Timeout of the request, in seconds.

    Returns:
        The document.

    Raises:
        RegistryError: The request failed, or the response is not a JSON
            document of at most `MAX_RESPONSE_SIZE` bytes with status `200`
            (also for redirects, which are not followed).
    """
    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException as exc:
        raise RegistryError(f"{url} unavailable: {type(exc).__name__}") from exc
    try:
        if response.status_code != 200:
            raise RegistryError(f"{url} unavailable: status {response.status_code}")
        content = bytearray()
        try:
            for chunk in response.iter_content(chunk_size=65536):
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_SIZE:
                    raise RegistryError(
                        f"{url}: response larger than {MAX_RESPONSE_SIZE} bytes"
                    )
        except requests.RequestException as exc:
            raise RegistryError(f"{url} unavailable: {type(exc).__name__}") from exc
    finally:
        response.close()
    try:
        return json.loads(content)
    except ValueError as exc:
        raise RegistryError(f"{url}: invalid JSON") from exc


def _same(first: object, second: object) -> bool:
    """Compare parts of API types, case-insensitively.

    Args:
        first: Part of an API type, e.g., `group`.
        second: Another part of an API type.

    Returns:
        Whether both are strings and equal, ignoring case.
    """
    return (
        isinstance(first, str)
        and isinstance(second, str)
        and first.casefold() == second.casefold()
    )


def _version_accepted(version: object, versions: list[str]) -> bool:
    """Check whether the TES version of a service is accepted.

    Args:
        version: Version of the service's API type.
        versions: Accepted versions, as prefixes (cf. `Discovery.versions`).

    Returns:
        Whether the version is one of `versions`, or starts with one of them
        and a dot.
    """
    return isinstance(version, str) and any(
        version == accepted or version.startswith(f"{accepted}.")
        for accepted in versions
    )


def _base_url(url: str) -> str:
    """Normalize the URL of a discovered backend, removing the TES API path.

    Args:
        url: URL of the service, as registered.

    Returns:
        The normalized URL (cf. :py:func:`foca.utils.urls.normalize_url`),
        without query, fragment and the API path `/ga4gh/tes/v1`.

    Raises:
        foca.utils.urls.InvalidURL: The URL is invalid, or not an `https`
            URL.
    """
    normalized = normalize_url(url, schemes=["https"], name="TES URLs")
    return normalized.removesuffix(TES_API_PATH)


@dataclass
class Selection:
    """Backends selected from the services of a registry.

    Attributes:
        backends: Discovered backends, in the order of the registry.
        rejected: Why verified TES services of an accepted version and
            environment were not used.
    """

    backends: list[DiscoveredBackend] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _verifications(document: object) -> dict[str, tuple[object, object, object]]:
    """Get the verifications of a registry, by service identifier.

    Args:
        document: Response of `GET /verifications`.

    Returns:
        URL, API group and artifact of each verified service, as verified.

    Raises:
        RegistryError: The response is not a list of objects with string
            identifiers.
    """
    if not isinstance(document, list):
        raise RegistryError("Verifications are not a list")
    verified: dict[str, tuple[object, object, object]] = {}
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("service_id"), str):
            raise RegistryError("Verifications without service identifier")
        api = item.get("type")
        api = api if isinstance(api, dict) else {}
        verified[item["service_id"]] = (
            item.get("url"),
            api.get("group"),
            api.get("artifact"),
        )
    return verified


def select_backends(
    services: object, verifications: object, conf: Discovery
) -> Selection:
    """Select the TES backends to use from the services of a registry.

    Args:
        services: Response of `GET /services`.
        verifications: Response of `GET /verifications`.
        conf: Discovery configuration.

    Returns:
        The selected backends, unique by URL, and why verified TES services
        of an accepted version and environment were rejected.

    Raises:
        RegistryError: A response is not a list (of objects, for
            verifications).
    """
    if not isinstance(services, list):
        raise RegistryError("Services are not a list")
    verified = _verifications(verifications)
    selection = Selection()
    urls: set[str] = set()
    for service in services:
        if not isinstance(service, dict):
            continue
        service_id = service.get("id")
        api = service.get("type")
        if not isinstance(service_id, str) or not isinstance(api, dict):
            continue
        if not (
            _same(api.get("group"), TES_GROUP)
            and _same(api.get("artifact"), TES_ARTIFACT)
        ):
            continue
        if service_id not in verified:
            logger.debug("Registry service %r is not verified", service_id)
            continue
        if not _version_accepted(api.get("version"), conf.versions):
            continue
        if conf.environments and service.get("environment") not in conf.environments:
            continue
        url = service.get("url")
        verified_url, group, artifact = verified[service_id]
        if not (
            isinstance(url, str)
            and url == verified_url
            and _same(group, TES_GROUP)
            and _same(artifact, TES_ARTIFACT)
        ):
            selection.rejected.append(
                f"{service_id!r}: URL or API type differ from the verified ones"
            )
            continue
        try:
            base_url = _base_url(url)
        except InvalidURL as exc:
            selection.rejected.append(f"{service_id!r}: {exc}")
            continue
        if not conf.allow_private_addresses:
            try:
                check_public_host(base_url)
            except BackendAddressNotAllowed as exc:
                selection.rejected.append(f"{service_id!r}: {exc}")
                continue
        if base_url in urls:
            continue
        urls.add(base_url)
        selection.backends.append(
            DiscoveredBackend(url=base_url, auth=conf.auth, service_id=service_id)
        )
    return selection


class BackendDiscovery:
    """Cache of the TES backends discovered in a service registry.

    Thread-safe; each process has its own cache. The registry is read by one
    thread at a time; while it is, other threads get the cached backends,
    except before the first read, which they wait for.
    """

    def __init__(self) -> None:
        """Create an empty cache."""
        self._lock = threading.Lock()
        self.clear()

    def clear(self) -> None:
        """Remove the discovered backends, so that the registry is read anew."""
        self._key: str | None = None
        self._backends: list[DiscoveredBackend] = []
        self._read = False
        self._next_refresh = 0.0
        self._rejected: set[str] = set()

    def reset_lock(self) -> None:
        """Replace the lock, e.g., in a forked process."""
        self._lock = threading.Lock()

    def get_backends(self, conf: Discovery) -> list[DiscoveredBackend]:
        """Get the discovered backends, reading the registry when due.

        Args:
            conf: Discovery configuration.

        Returns:
            The discovered backends; none if discovery is off, or before the
            registry was read successfully.
        """
        if conf.registry_url is None:
            return []
        key = conf.model_dump_json()
        backends = self._backends
        if key == self._key and time.monotonic() < self._next_refresh:
            return list(backends)
        # waits for the read if the configuration changed (e.g., in tests) or
        # the registry was never read; otherwise, uses the cached backends
        # while another thread reads it
        if not self._lock.acquire(blocking=key != self._key or not self._read):
            return list(backends)
        try:
            if key != self._key:
                self.clear()
                self._key = key
            if time.monotonic() >= self._next_refresh:
                self._refresh(conf=conf, registry_url=conf.registry_url)
            return list(self._backends)
        finally:
            self._lock.release()

    def _refresh(self, conf: Discovery, registry_url: str) -> None:
        """Read the registry, keeping the last good backends on errors.

        Args:
            conf: Discovery configuration.
            registry_url: Base URL of the registry's API.
        """
        self._next_refresh = time.monotonic() + conf.refresh_interval
        try:
            verifications = fetch_json(f"{registry_url}/verifications", conf.timeout)
            services = fetch_json(f"{registry_url}/services", conf.timeout)
            selection = select_backends(
                services=services, verifications=verifications, conf=conf
            )
        except RegistryError as exc:
            logger.warning(
                "TES backends not discovered at registry %s (%s); keeping %d"
                " backends discovered earlier",
                registry_url,
                exc,
                len(self._backends),
            )
            return
        for reason in selection.rejected:
            if reason not in self._rejected:
                logger.warning("Registry service not used: %s", reason)
        self._rejected = set(selection.rejected)
        urls = [backend.url for backend in selection.backends]
        if not self._read or urls != [backend.url for backend in self._backends]:
            logger.info(
                "TES backends discovered at registry %s: %s",
                registry_url,
                ", ".join(urls) or "none",
            )
        self._backends = selection.backends
        self._read = True


discovery = BackendDiscovery()
os.register_at_fork(after_in_child=discovery.reset_lock)


def _url_key(url: str) -> str:
    """Get the key of a backend URL, to compare URLs.

    Args:
        url: URL of a backend.

    Returns:
        The normalized URL, or, if it is invalid, the URL without trailing
        slashes.
    """
    try:
        return normalize_url(url)
    except ValueError:
        return url.rstrip("/")


def get_backends() -> list[TesBackend]:
    """Get the TES backends that tasks may be forwarded to.

    Must be called in an app context.

    Returns:
        The backends of `custom.tes.service_list`, followed by those
        discovered in the registry (`custom.tes.discovery`) with other URLs.
    """
    conf = get_custom_config().tes
    backends: list[TesBackend] = list(conf.service_list)
    known = {_url_key(backend.url) for backend in backends}
    for backend in discovery.get_backends(conf=conf.discovery):
        if _url_key(backend.url) not in known:
            backends.append(backend)
    return backends


def service_info_fields() -> dict[str, Any]:
    """Get the service info fields with the TES backends of proTES.

    Served with the service info (`service_info.runtime_fields`).

    Returns:
        The URLs of the TES backends (`backends`), cf.
        :py:func:`get_backends`.
    """
    return {"backends": [backend.url for backend in get_backends()]}
