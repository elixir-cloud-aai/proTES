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
  host is checked on discovery and before each request to the backend, and
  requests to discovered backends don't follow redirects.

The registry, configured by the deployment, is the trust anchor: it is read
without access token and redirects are not followed. Each process (e.g.,
each Gunicorn or Celery worker) caches the discovered backends, refreshing
them every `refresh_interval` seconds, and keeps the last good list while
the registry is unreachable or returns invalid responses (retrying after
`RETRY_INTERVAL` seconds at most), and, for one more read, backends whose
hosts can't be resolved for the time being. Changes are logged. Backends of
`service_list` take precedence over discovered ones with the same URL.
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
from pro_tes.exceptions import BackendAddressNotAllowed, HostNotResolved, RegistryError
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
# Maximum seconds until the registry is read again after a failed read
RETRY_INTERVAL = 30
# Minimum seconds between reads of the registry for backends that are not in
# the cache (cf. `BackendDiscovery.get_backends()`)
MIN_REFRESH_INTERVAL = 10
# Prefixes of IPv6 addresses that embed IPv4 addresses in their last 32
# bits: IPv4/IPv6 translation (RFC 6052) and IPv4-compatible addresses
# (deprecated, RFC 4291)
_EMBEDDING_PREFIXES = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("::/96"),
)


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check whether an IP address is public.

    Args:
        address: IP address.

    Returns:
        Whether the address is globally reachable (as per the IANA
        special-purpose address registries) and not a multicast address;
        IPv4 addresses embedded in IPv6 addresses (IPv4-mapped, NAT64 and
        IPv4-compatible ones) are checked themselves.
    """
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif any(address in prefix for prefix in _EMBEDDING_PREFIXES):
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return address.is_global and not address.is_multicast


def check_public_host(url: str) -> None:
    """Check that the host of a URL has public addresses only.

    Host names are resolved; all their addresses must be public (cf.
    :py:func:`is_public_address`). Resolution is not bounded by the timeout
    of the registry (`Discovery.timeout`), but by that of the system's
    resolver.

    Args:
        url: URL.

    Raises:
        pro_tes.exceptions.HostNotResolved: The host can't be resolved, or
            has no address.
        pro_tes.exceptions.BackendAddressNotAllowed: The host has an address
            that is not public, or a zone identifier.
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
            raise HostNotResolved(
                f"Host of {url} not resolved: {type(exc).__name__}"
            ) from exc
        addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise HostNotResolved(f"Host of {url} has no address")
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
        pro_tes.exceptions.RegistryError: The request failed, or the
            response is not a JSON document of at most `MAX_RESPONSE_SIZE`
            bytes (and nested at most as deeply as the JSON parser allows)
            with status `200` (also for redirects, which are not followed).
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
    except (ValueError, RecursionError) as exc:
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


def _is_tes(api: object) -> bool:
    """Check whether an API type is that of TES.

    Args:
        api: API type of a service (`type`).

    Returns:
        Whether the group is `org.ga4gh` and the artifact `tes`, ignoring
        case.
    """
    return (
        isinstance(api, dict)
        and _same(api.get("group"), TES_GROUP)
        and _same(api.get("artifact"), TES_ARTIFACT)
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


def _wanted(service: dict[str, Any], conf: Discovery) -> bool:
    """Check whether a service is a TES of an accepted version and environment.

    Args:
        service: Service, as listed by the registry.
        conf: Discovery configuration.

    Returns:
        Whether the service is a TES service of one of `conf.versions`, and,
        if `conf.environments` is set, of one of them.
    """
    api = service.get("type")
    return (
        isinstance(api, dict)
        and _is_tes(api)
        and _version_accepted(api.get("version"), conf.versions)
        and (not conf.environments or service.get("environment") in conf.environments)
    )


def base_url(url: str) -> str:
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


def url_key(url: str) -> str:
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


@dataclass
class Selection:
    """Backends selected from the services of a registry.

    Attributes:
        backends: Discovered backends, in the order of the registry.
        rejected: Why verified TES services of an accepted version and
            environment were not used.
        unresolved: Identifiers of the services whose backends were kept
            although their hosts could not be resolved.
    """

    backends: list[DiscoveredBackend] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    unresolved: set[str] = field(default_factory=set)


def _verifications(document: object) -> dict[str, tuple[object, object]]:
    """Get the verifications of a registry, by service identifier.

    Args:
        document: Response of `GET /verifications`.

    Returns:
        URL and API type of each verified service, as verified.

    Raises:
        pro_tes.exceptions.RegistryError: The response is not a list of
            objects with string identifiers.
    """
    if not isinstance(document, list):
        raise RegistryError("Verifications are not a list")
    verified: dict[str, tuple[object, object]] = {}
    for item in document:
        if not isinstance(item, dict) or not isinstance(item.get("service_id"), str):
            raise RegistryError("Verifications without service identifier")
        verified[item["service_id"]] = (item.get("url"), item.get("type"))
    return verified


def _accept(
    service_id: str,
    url: str,
    conf: Discovery,
    previous: dict[str, DiscoveredBackend],
    unresolved: set[str],
) -> DiscoveredBackend:
    """Get the backend of a verified TES service, checking its URL.

    Args:
        service_id: Identifier of the service.
        url: URL of the service, as registered and verified.
        conf: Discovery configuration.
        previous: Backends discovered before, by service identifier.
        unresolved: Identifiers of the services whose backends are kept
            although their hosts can't be resolved; updated.

    Returns:
        The backend; also if its host can't be resolved for the time being,
        if it was discovered with the same URL before (and is added to
        `unresolved`).

    Raises:
        foca.utils.urls.InvalidURL: The URL is invalid, or not an `https`
            URL.
        pro_tes.exceptions.BackendAddressNotAllowed: The host of the URL
            has an address that is not public (unless allowed), or can't be
            resolved.
    """
    backend = DiscoveredBackend(
        url=base_url(url),
        auth=conf.auth,
        service_id=service_id,
    )
    if conf.allow_private_addresses:
        return backend
    try:
        check_public_host(backend.url)
    except HostNotResolved:
        if previous.get(service_id) != backend:
            raise
        # checked before each request, too
        logger.warning(
            "Host of TES backend %s not resolved; keeping it for this read of"
            " the registry",
            backend.url,
        )
        unresolved.add(service_id)
    return backend


def select_backends(
    services: object,
    verifications: object,
    conf: Discovery,
    previous: Iterable[DiscoveredBackend] = (),
) -> Selection:
    """Select the TES backends to use from the services of a registry.

    Services that are not TES services of an accepted version and
    environment, and unverified ones, are skipped; verified ones that can't
    be used are rejected, with the reason.

    Args:
        services: Response of `GET /services`.
        verifications: Response of `GET /verifications`.
        conf: Discovery configuration.
        previous: Backends discovered before, which are kept for this read
            if their hosts can't be resolved for the time being.

    Returns:
        The selected backends, unique by URL, and why verified TES services
        of an accepted version and environment were rejected.

    Raises:
        pro_tes.exceptions.RegistryError: A response is not a list (of
            objects, for verifications).
    """
    if not isinstance(services, list):
        raise RegistryError("Services are not a list")
    verified = _verifications(verifications)
    known = {backend.service_id: backend for backend in previous}
    selection = Selection()
    urls: set[str] = set()
    for service in services:
        if not isinstance(service, dict) or not _wanted(service, conf):
            continue
        service_id = service.get("id")
        if not isinstance(service_id, str) or service_id not in verified:
            logger.debug("Registry service %r is not verified", service_id)
            continue
        url = service.get("url")
        verified_url, verified_api = verified[service_id]
        if not isinstance(url, str) or url != verified_url or not _is_tes(verified_api):
            selection.rejected.append(
                f"{service_id!r}: URL or API type differ from the verified ones"
            )
            continue
        try:
            backend = _accept(
                service_id,
                url=url,
                conf=conf,
                previous=known,
                unresolved=selection.unresolved,
            )
        except (InvalidURL, BackendAddressNotAllowed) as exc:
            selection.rejected.append(f"{service_id!r}: {exc}")
            continue
        if backend.url not in urls:
            urls.add(backend.url)
            selection.backends.append(backend)
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
        self._conf: Discovery | None = None
        self._backends: list[DiscoveredBackend] = []
        self._read = False
        self._last_read = 0.0
        self._next_read = 0.0
        self._rejected: set[str] = set()
        self._unresolved: set[str] = set()

    def clear(self) -> None:
        """Remove the discovered backends, so that the registry is read anew."""
        self._conf = None
        self._backends = []
        self._read = False
        self._last_read = 0.0
        self._next_read = 0.0
        self._rejected = set()
        self._unresolved = set()

    def _reset_lock(self) -> None:
        """Replace the lock, e.g., in a forked process."""
        self._lock = threading.Lock()

    def get_backends(
        self, conf: Discovery, refresh: bool = False
    ) -> list[DiscoveredBackend]:
        """Get the discovered backends, reading the registry when due.

        Args:
            conf: Discovery configuration.
            refresh: Whether to read the registry now, unless it was read in
                the last `MIN_REFRESH_INTERVAL` seconds, e.g., for a backend
                that another process discovered already.

        Returns:
            The discovered backends; none if discovery is off, or before the
            registry was read successfully.
        """
        if not conf.enabled:
            return []
        backends = self._backends
        if conf == self._conf and not self._due(refresh):
            return list(backends)
        # waits for the read if the configuration changed (e.g., in tests) or
        # the registry was never read; otherwise, uses the cached backends
        # while another thread reads it
        if not self._lock.acquire(blocking=conf != self._conf or not self._read):
            return list(backends)
        try:
            if conf != self._conf:
                self.clear()
                self._conf = conf.model_copy(deep=True)
            if self._due(refresh):
                self._refresh(conf=conf)
            return list(self._backends)
        finally:
            self._lock.release()

    def _due(self, refresh: bool) -> bool:
        """Check whether the registry is to be read.

        Args:
            refresh: Whether a read is requested (cf. `get_backends()`).

        Returns:
            Whether the next read is due, or a read is requested and the last
            one is at least `MIN_REFRESH_INTERVAL` seconds ago.
        """
        now = time.monotonic()
        return now >= self._next_read or (
            refresh and now - self._last_read >= MIN_REFRESH_INTERVAL
        )

    def _refresh(self, conf: Discovery) -> None:
        """Read the registry, keeping the last good backends on errors.

        Errors of any kind are logged, so that registry responses never
        fail the requests that read the registry.

        Args:
            conf: Discovery configuration, with `registry_url`.
        """
        registry_url = str(conf.registry_url)
        now = time.monotonic()
        self._last_read = now
        self._next_read = now + min(conf.refresh_interval, RETRY_INTERVAL)
        try:
            verifications = fetch_json(f"{registry_url}/verifications", conf.timeout)
            services = fetch_json(f"{registry_url}/services", conf.timeout)
            selection = select_backends(
                services=services,
                verifications=verifications,
                conf=conf,
                # backends kept once already are not kept again
                previous=[
                    backend
                    for backend in self._backends
                    if backend.service_id not in self._unresolved
                ],
            )
        except Exception as exc:
            # `RegistryError` for invalid responses; others are bugs
            log = logger.warning if isinstance(exc, RegistryError) else logger.exception
            log(
                "TES backends not discovered at registry %s (%s); keeping %d"
                " backends discovered earlier",
                registry_url,
                exc,
                len(self._backends),
            )
            return
        self._next_read = now + conf.refresh_interval
        for reason in selection.rejected:
            if reason not in self._rejected:
                logger.warning("Registry service not used: %s", reason)
        self._rejected = set(selection.rejected)
        self._unresolved = selection.unresolved
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
os.register_at_fork(after_in_child=discovery._reset_lock)


def get_backends(refresh: bool = False) -> list[TesBackend]:
    """Get the TES backends that tasks may be forwarded to.

    Must be called in an app context.

    Args:
        refresh: Whether to read the registry now (rate-limited, cf.
            :py:meth:`BackendDiscovery.get_backends`).

    Returns:
        The backends of `custom.tes.service_list`, followed by those
        discovered in the registry (`custom.tes.discovery`) with other URLs.
    """
    conf = get_custom_config().tes
    backends: list[TesBackend] = list(conf.service_list)
    known = {url_key(backend.url) for backend in backends}
    for backend in discovery.get_backends(conf=conf.discovery, refresh=refresh):
        if url_key(backend.url) not in known:
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
