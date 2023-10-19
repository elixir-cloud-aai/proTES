"""Module for distance-based task distribution logic."""

import logging
from socket import gaierror, gethostbyname
from typing import Optional
from urllib.parse import urlparse

import flask
from geopy.distance import geodesic  # type: ignore
from ip2geotools.errors import InvalidRequestError  # type: ignore
from ip2geotools.databases.noncommercial import DbIpCity  # type: ignore
from ip2geotools.models import IpLocation  # type: ignore
from pydantic import (  # pragma pylint: disable=no-name-in-module
    AnyUrl,
    BaseModel,
    HttpUrl,
)

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)
from pro_tes.utils.misc import strip_auth

logger = logging.getLogger(__name__)

# pragma pylint: disable=too-few-public-methods


class TesStats(BaseModel):
    """TES statistics.

    Attributes:
        total distance: The total geodesic IP distance between the TES instance
            and the task's inputs.
    """

    total_distance: Optional[float] = None


class TesInstance(BaseModel):
    """TES instance information.

    Attributes:
        location: TES instance IP location.
        stats: TES instance statistics.
    """

    location: Optional[IpLocation] = None
    stats: TesStats


class TaskInput(BaseModel):
    """Task input information.

    Attributes:
        location: Input IP location.
    """

    location: Optional[IpLocation] = None


class TaskSummary(BaseModel):
    """Summary of TES instances and corresponding statistics.

    Attributs:
        inputs: A dictionary of task input URIs and corresponding information.
        tes_instances: A dictionary of TES instance URIs and corresponding
            information.
    """

    inputs: dict[AnyUrl, TaskInput]
    tes_instances: dict[HttpUrl, TesInstance]


class TaskDistributionDistance(TaskDistributionBaseClass):
    """Distance-based task distribution middleware.

    Sorts the available TES instances by the sum of minimum geodesic distances
    between a given TES instance and all of the task'sinputs, in ascending
    order. Distances are calculated based on IP geolocations of the TES
    instance and the inputs.

    Attributes:
        tes_urls: TES instance best suited for TES task.
        input_uris: A list of input URIs from the incoming request.
        tes_summary: Summary of TES instances and corresponding statistics.
    """

    def __init__(self) -> None:
        """Class constructor."""
        super().__init__()
        self.task_summary: TaskSummary

    def _set_tes_urls(
        self,
        tes_urls: list[HttpUrl],
        request: flask.Request,
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            request: Request object to be modified.
        """
        self._set_tes_instances(tes_urls=tes_urls)
        self._set_task_inputs(request=request)
        self._set_locations()
        self._set_distances()
        self.tes_urls = self._rank_tes_instances()

    def _set_tes_instances(self, tes_urls: list[HttpUrl]) -> None:
        """Set TES instances.

        Args:
            tes_urls: List of TES URLs.
        """
        for url in list(set(tes_urls)):
            self.task_summary.tes_instances[url] = TesInstance()

    def _set_task_inputs(self, request: flask.Request) -> None:
        """Set task inputs.

        Args:
            request: Request object to be modified.

        Raises:
            MiddlewareException: If request has no JSON payload or if no input
                URIs are available.
        """
        if request.json is None:
            raise MiddlewareException("Request has no JSON payload.")
        input_uris = list(
            {
                input_value.get("url")
                for input_value in request.json.get("inputs", [])
                if input_value.get("url") is not None
            }
        )
        if not input_uris:
            raise MiddlewareException("No input URIs available.")
        for uri in list(set(input_uris)):
            self.task_summary.inputs[uri] = TaskInput()

    def _set_locations(self) -> None:
        """Set IP locations for TES instances and task inputs."""
        tes_urls = list(self.task_summary.tes_instances.keys())
        input_uris = list(self.task_summary.inputs.keys())
        uris_unique: list[AnyUrl] = list(set(tes_urls + input_uris))
        ips: dict[AnyUrl, str] = self._get_ips(*uris_unique)
        locations = self._get_ip_locations(*ips.values())
        for url in tes_urls:
            self.task_summary.tes_instances[url].location = locations[ips[url]]
        for uri in input_uris:
            self.task_summary.inputs[uri].location = locations[ips[uri]]

    def _set_distances(self) -> None:
        """Set distances between TES instances and task inputs.

        Raises:
            MiddlewareException: If an IP location is not available for a TES
                instance or input object.
        """
        locations_inputs: list[IpLocation] = []
        for uri, obj in self.task_summary.inputs.items():
            if obj.location is None:
                raise MiddlewareException(
                    f"IP location not available for input at URI: {uri}"
                )
            locations_inputs.append(obj.location)
        for url, instance in self.task_summary.tes_instances.items():
            if instance.location is None:
                raise MiddlewareException(
                    f"IP location not available for TES instance at URL: {url}"
                )
            distances = self._get_distances(
                node=instance.location,
                leaves=locations_inputs,
            )
            instance.stats = TesStats(total_distance=sum(distances))

    def _rank_tes_instances(self) -> list[HttpUrl]:
        """Rank TES instances by physical proximity to the task's inputs.

        Returns:
            A list of TES URIs ranked by total distance to the task's inputs,
                in ascending order.

        Raises:
            MiddlewareException: If total distance is not available for a TES
                instance.
        """
        distances: dict[HttpUrl, float] = {}
        for url, instance in self.task_summary.tes_instances.items():
            if instance.stats.total_distance is None:
                raise MiddlewareException(
                    "Total distance not available for TES instance at URL:"
                    f" {url}"
                )
            distances[url] = instance.stats.total_distance
        return list(
            dict(sorted(distances.items(), key=lambda item: item[1])).keys()
        )

    @staticmethod
    def _get_ips(*args: AnyUrl) -> dict[AnyUrl, str]:
        """Get IP addresses for one or more URIs.

        Args:
            *args: URIs.

        Returns:
            Dictionary of URIs and their IP addresses.

        Raises:
            MiddlewareException: If IP address cannot be determined for a URI.
        """
        ips: dict[AnyUrl, str] = {}
        for uri in args:
            try:
                ips[uri] = gethostbyname(urlparse(strip_auth(uri)).netloc)
            except gaierror as exc:
                raise MiddlewareException(
                    f"Could not determine IP address for URI: {uri}"
                ) from exc
        return ips

    @staticmethod
    def _get_ip_locations(*args: str) -> dict[str, IpLocation]:
        """Get locations of IP addresses.

        Args:
            *args: IP addresses.

        Returns:
            Dictionary of unique IP addresses and their locations.

        Raises:
            MiddlewareException: If location cannot be determined for an IP.
        """
        locations: dict[str, IpLocation] = {}
        for ip_addr in args:
            try:
                locations[ip_addr] = DbIpCity.get(ip_addr)
            except InvalidRequestError as exc:
                raise MiddlewareException(
                    f"Could not determine location for IP: {ip_addr}"
                ) from exc
        return locations

    @staticmethod
    def _get_distances(
        node: IpLocation,
        leaves: list[IpLocation],
    ) -> list[float]:
        """Get distances between a node and a list of leaves.

        Args:
            node: Node location.
            leaves: List of leaf locations.

        Returns:
            List of distances between the node and the leaves, in kilometers.
        """
        return [
            geodesic(
                (node.latitude, node.longitude),
                (leaf.latitude, leaf.longitude),
            ).km
            for leaf in leaves
        ]
