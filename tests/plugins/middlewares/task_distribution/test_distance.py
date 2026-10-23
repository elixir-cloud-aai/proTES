"""Tests of the distance-based task distribution middleware."""

import socket
from typing import Any, ClassVar

import flask
from geopy.exc import GeocoderServiceError
import pytest
import requests

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution import distance
from pro_tes.plugins.middlewares.task_distribution.distance import (
    Location,
    TaskDistributionDistance,
    locate_ip,
)

# Hosts, their IP addresses (documentation ranges, RFC 5737) and locations
# (latitude, longitude)
HOSTS = {
    "tes-basel.example.org": "192.0.2.1",
    "tes-helsinki.example.org": "192.0.2.2",
    "data-zurich.example.org": "198.51.100.1",
    "data-bern.example.org": "198.51.100.2",
}
LOCATIONS = {
    "192.0.2.1": (47.56, 7.59),
    "192.0.2.2": (60.17, 24.94),
    "198.51.100.1": (47.37, 8.54),
    "198.51.100.2": (46.95, 7.45),
}
TES_URLS = ["https://tes-helsinki.example.org", "https://tes-basel.example.org"]
INPUTS = [
    {"url": "s3://data-zurich.example.org/in1", "path": "/in1"},
    {"url": "https://user:secret@data-bern.example.org/in2", "path": "/in2"},
    {"content": "hello", "path": "/greeting"},
]


def resolve(host: str) -> str:
    """Resolve the hosts of the tests, as `socket.gethostbyname()` does.

    Raises:
        socket.gaierror: The host is unknown.
    """
    try:
        return HOSTS[host]
    except KeyError:
        raise socket.gaierror(f"Unknown host: {host}") from None


def locate(ip_address: str) -> Location:
    """Locate the IP addresses of the tests, as `locate_ip()` does.

    Raises:
        MiddlewareException: The IP address is unknown.
    """
    if ip_address not in LOCATIONS:
        raise MiddlewareException(f"Could not determine location for IP: {ip_address}")
    latitude, longitude = LOCATIONS[ip_address]
    return Location(latitude=latitude, longitude=longitude)


@pytest.fixture
def located(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Locations of the hosts of the tests; records located IP addresses."""
    calls: list[str] = []

    def get(ip_address: str) -> Location:
        """Locate and record an IP address."""
        calls.append(ip_address)
        return locate(ip_address)

    monkeypatch.setattr(distance, "gethostbyname", resolve)
    monkeypatch.setattr(distance, "locate_ip", get)
    return calls


def rank(inputs: list[dict[str, Any]], tes_urls: list[str] = TES_URLS) -> list[str]:
    """Rank TES backends for a task with inputs.

    Args:
        inputs: Inputs of the task.
        tes_urls: URLs of the TES backends.

    Returns:
        The backends, nearest first.
    """
    app = flask.Flask(__name__)
    with app.test_request_context(json={"inputs": inputs}):
        middleware = TaskDistributionDistance()
        middleware._set_tes_urls(tes_urls=tes_urls, request=flask.request)
        return middleware.tes_urls


def test_rank(located: list[str]) -> None:
    """Backends are ranked by their total distance to the inputs.

    Credentials in input URLs are ignored, each IP address is located once.
    """
    assert rank(INPUTS) == list(reversed(TES_URLS))
    assert sorted(located) == sorted(LOCATIONS)


def test_rank_duplicates(located: list[str]) -> None:
    """Duplicate backends and inputs are considered once."""
    assert rank(INPUTS * 2, TES_URLS * 2) == list(reversed(TES_URLS))


def test_no_input_urls(located: list[str]) -> None:
    """Tasks without input URLs can't be distributed by distance."""
    with pytest.raises(MiddlewareException, match="No input URIs available"):
        rank([{"content": "hello", "path": "/greeting"}])
    with pytest.raises(MiddlewareException, match="No input URIs available"):
        rank([])


def test_unknown_host(located: list[str]) -> None:
    """Backends or inputs whose host can't be resolved fail the middleware."""
    with pytest.raises(MiddlewareException, match="Could not determine IP address"):
        rank([{"url": "s3://unknown.example.org/in", "path": "/in"}])


def test_unknown_location(monkeypatch: pytest.MonkeyPatch, located: list[str]) -> None:
    """Backends or inputs that can't be located fail the middleware."""
    monkeypatch.setitem(HOSTS, "data-moon.example.org", "203.0.113.1")
    with pytest.raises(MiddlewareException, match="Could not determine location"):
        rank([{"url": "s3://data-moon.example.org/in", "path": "/in"}])


def test_missing_location() -> None:
    """Backends or inputs without location can't be ranked."""
    middleware = TaskDistributionDistance()
    middleware._set_tes_instances(tes_urls=TES_URLS)
    uri = "s3://data-zurich.example.org/in1"
    middleware.task_summary.inputs[uri] = distance.TaskInput()
    with pytest.raises(MiddlewareException, match="not available for input"):
        middleware._set_distances()
    middleware.task_summary.inputs[uri] = distance.TaskInput(
        location=locate("198.51.100.1")
    )
    with pytest.raises(MiddlewareException, match="not available for TES instance"):
        middleware._set_distances()
    with pytest.raises(MiddlewareException, match="Total distance not available"):
        middleware._rank_tes_instances()


def test_no_json() -> None:
    """Requests without a JSON body fail the middleware."""
    app = flask.Flask(__name__)
    with (
        app.test_request_context(data="null", content_type="application/json"),
        pytest.raises(MiddlewareException, match="no JSON payload"),
    ):
        TaskDistributionDistance()._set_tes_urls(
            tes_urls=TES_URLS, request=flask.request
        )


# Places of IP addresses, as DB-IP's API returns them, and their coordinates,
# as Nominatim returns them for the queries of `locate_ip()`
PLACE = {
    "ipAddress": "192.0.2.1",
    "countryCode": "CH",
    "stateProv": "Basel-City",
    "city": "Basel",
}
QUERY = "Basel, Basel-City CH"
FALLBACK_QUERY = "Basel, CH"


class FakeResponse:
    """Fake response of DB-IP's API (`requests.get()`)."""

    def __init__(self, data: Any, status_code: int = 200) -> None:
        """Create a response with a JSON body (`ValueError` if not JSON)."""
        self.data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise an error for error status codes, as `requests` does.

        Raises:
            requests.HTTPError: The status code is 400 or higher.
        """
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self) -> Any:
        """Get the JSON body.

        Raises:
            ValueError: The body is not JSON.
        """
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class FakePoint:
    """Fake result of Nominatim (`geopy.location.Location`)."""

    def __init__(self, latitude: float, longitude: float) -> None:
        """Create a result with coordinates."""
        self.latitude = latitude
        self.longitude = longitude


class FakeNominatim:
    """Fake Nominatim geocoder: geocodes the queries of `RESULTS`.

    Records the arguments it is created with, and the queries.
    """

    RESULTS: ClassVar[dict[str, FakePoint]] = {QUERY: FakePoint(47.56, 7.59)}
    created: ClassVar[list[dict[str, Any]]] = []
    queries: ClassVar[list[str]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, **kwargs: Any) -> None:
        """Create a geocoder; records the arguments."""
        self.created.append(kwargs)

    def geocode(self, query: str) -> FakePoint | None:
        """Geocode a query.

        Raises:
            Exception: `error`, if set.
        """
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.RESULTS.get(query)


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Fake geolocation services; records the requests to DB-IP's API."""
    requested: list[tuple[str, dict[str, Any]]] = []

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Get the place of `192.0.2.1`, or an error of DB-IP's API."""
        requested.append((url, kwargs))
        if url.endswith("/192.0.2.1"):
            return FakeResponse(PLACE)
        return FakeResponse({"error": "invalid address"})

    monkeypatch.setattr(distance.requests, "get", get)
    monkeypatch.setattr(distance, "Nominatim", FakeNominatim)
    monkeypatch.setattr(FakeNominatim, "created", [])
    monkeypatch.setattr(FakeNominatim, "queries", [])
    monkeypatch.setattr(FakeNominatim, "error", None)
    return requested


def test_locate_ip(services: list[tuple[str, dict[str, Any]]]) -> None:
    """IP addresses are located by DB-IP's API (HTTPS) and Nominatim.

    Both services are called with a timeout, and Nominatim with a user agent
    naming the app, as its usage policy requires.
    """
    assert locate_ip("192.0.2.1") == Location(latitude=47.56, longitude=7.59)
    assert services == [
        ("https://api.db-ip.com/v2/free/192.0.2.1", {"timeout": distance.TIMEOUT})
    ]
    assert FakeNominatim.created == [
        {"user_agent": distance.USER_AGENT, "timeout": distance.TIMEOUT}
    ]
    assert FakeNominatim.queries == [QUERY]


def test_locate_ip_fallback_query(
    monkeypatch: pytest.MonkeyPatch, services: list[tuple[str, dict[str, Any]]]
) -> None:
    """Places that Nominatim can't find are looked up without the region."""
    monkeypatch.setattr(
        FakeNominatim, "RESULTS", {FALLBACK_QUERY: FakePoint(47.56, 7.59)}
    )
    assert locate_ip("192.0.2.1") == Location(latitude=47.56, longitude=7.59)
    assert FakeNominatim.queries == [QUERY, FALLBACK_QUERY]


def test_locate_ip_unknown_place(
    monkeypatch: pytest.MonkeyPatch, services: list[tuple[str, dict[str, Any]]]
) -> None:
    """IP addresses whose place Nominatim can't find can't be located."""
    monkeypatch.setattr(FakeNominatim, "RESULTS", {})
    with pytest.raises(MiddlewareException, match="Could not determine location"):
        locate_ip("192.0.2.1")
    assert FakeNominatim.queries == [QUERY, FALLBACK_QUERY]


def test_locate_ip_unknown_address(
    services: list[tuple[str, dict[str, Any]]],
) -> None:
    """IP addresses that DB-IP doesn't know can't be located."""
    with pytest.raises(MiddlewareException, match="Could not determine location"):
        locate_ip("203.0.113.1")
    assert not FakeNominatim.created


@pytest.mark.parametrize(
    "response",
    [
        requests.ConnectionError("unreachable"),
        FakeResponse({"error": "too many requests"}, status_code=429),
        FakeResponse(ValueError("not JSON")),
        FakeResponse(["not", "an", "object"]),
    ],
    ids=["unreachable", "error status", "not JSON", "not an object"],
)
def test_locate_ip_service_error(
    monkeypatch: pytest.MonkeyPatch,
    services: list[tuple[str, dict[str, Any]]],
    response: FakeResponse | Exception,
) -> None:
    """Errors of DB-IP's API fail the location of an IP address."""

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Fail, or return an invalid response."""
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(distance.requests, "get", get)
    with pytest.raises(MiddlewareException, match="Could not determine location"):
        locate_ip("192.0.2.1")
    assert not FakeNominatim.created


def test_locate_ip_geocoder_error(
    monkeypatch: pytest.MonkeyPatch, services: list[tuple[str, dict[str, Any]]]
) -> None:
    """Errors of Nominatim fail the location of an IP address."""
    monkeypatch.setattr(FakeNominatim, "error", GeocoderServiceError("unavailable"))
    with pytest.raises(MiddlewareException, match="Could not determine location"):
        locate_ip("192.0.2.1")


def test_locate_ip_escapes_address(
    services: list[tuple[str, dict[str, Any]]],
) -> None:
    """Addresses are escaped in the URL of DB-IP's API."""
    with pytest.raises(MiddlewareException):
        locate_ip("../192.0.2.1")
    assert services[0][0] == "https://api.db-ip.com/v2/free/..%2F192.0.2.1"
