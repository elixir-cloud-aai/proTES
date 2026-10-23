"""Tests of the distance-based task distribution middleware."""

import socket
from typing import Any

import flask
from ip2geotools.errors import InvalidRequestError
from ip2geotools.models import IpLocation
import pytest

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution import distance
from pro_tes.plugins.middlewares.task_distribution.distance import (
    TaskDistributionDistance,
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


def locate(ip_address: str, **kwargs: Any) -> IpLocation:
    """Locate the IP addresses of the tests, as `DbIpCity.get()` does.

    Raises:
        InvalidRequestError: The IP address is unknown.
    """
    if ip_address not in LOCATIONS:
        raise InvalidRequestError
    location = IpLocation(ip_address)
    location.latitude, location.longitude = LOCATIONS[ip_address]
    return location


@pytest.fixture
def located(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Locations of the hosts of the tests; records located IP addresses."""
    calls: list[str] = []

    def get(ip_address: str, **kwargs: Any) -> IpLocation:
        """Locate and record an IP address."""
        calls.append(ip_address)
        return locate(ip_address)

    monkeypatch.setattr(distance, "gethostbyname", resolve)
    monkeypatch.setattr(distance.DbIpCity, "get", get)
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
