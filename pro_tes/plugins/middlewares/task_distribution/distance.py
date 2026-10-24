"""Module for distance-based task distribution logic."""

from ipaddress import ip_address as ip_address_info
import logging
from socket import gaierror, gethostbyname
from typing import Any
from urllib.parse import quote, urlparse

from geopy.distance import geodesic
from geopy.exc import GeopyError
from geopy.geocoders import Nominatim
from pydantic import BaseModel
import requests

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)
from pro_tes.utils.misc import strip_auth

logger = logging.getLogger(__name__)

# Geolocation services: DB-IP's free API gives the place (city, region and
# country) of an IP address, and Nominatim (OpenStreetMap) its coordinates
DB_IP_URL = "https://api.db-ip.com/v2/free/{ip}"
# Nominatim's usage policy requires a user agent that names the application
USER_AGENT = "pro-tes"
# Timeout of each request to the geolocation services, in seconds
TIMEOUT = 10


class Location(BaseModel):
    """Geographic location.

    Attributes:
        latitude: Latitude, in degrees.
        longitude: Longitude, in degrees.
    """

    latitude: float
    longitude: float


def locate_ip(ip_address: str) -> Location:
    """Locate an IP address.

    Gets the place of the IP address (city, region and country) from DB-IP's
    free API, and its coordinates from Nominatim; places that Nominatim
    doesn't find are looked up without the region. Addresses that are not
    global (e.g., private or loopback ones) are not sent to the services, and
    places without city or country (e.g., of reserved addresses) are not
    looked up, as they have no meaningful location.

    Args:
        ip_address: IP address.

    Returns:
        The location of the IP address.

    Raises:
        MiddlewareException: The location can't be determined, e.g., as a
            service is unavailable, or doesn't know the IP address or its
            place.
    """
    error = f"Could not determine location for IP: {ip_address}"
    try:
        is_global = ip_address_info(ip_address).is_global
    except ValueError as exc:
        raise MiddlewareException(error) from exc
    if not is_global:
        raise MiddlewareException(f"{error} (not a global address)")
    try:
        response = requests.get(
            DB_IP_URL.format(ip=quote(ip_address, safe="")), timeout=TIMEOUT
        )
        response.raise_for_status()
        place = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MiddlewareException(error) from exc
    if not isinstance(place, dict) or "error" in place:
        raise MiddlewareException(error)
    city, region, country = (
        str(place.get(key) or "").strip()
        for key in ("city", "stateProv", "countryCode")
    )
    # `ZZ`: unknown country
    if not city or not country or country == "ZZ":
        raise MiddlewareException(error)
    queries = [f"{city}, {country}"]
    if region:
        queries.insert(0, f"{city}, {region} {country}")
    geocoder = Nominatim(user_agent=USER_AGENT, timeout=TIMEOUT)
    for query in queries:
        try:
            point = geocoder.geocode(query)
        except GeopyError as exc:
            raise MiddlewareException(error) from exc
        if point is not None:
            return Location(latitude=point.latitude, longitude=point.longitude)
    raise MiddlewareException(error)


class TesStats(BaseModel):
    """TES statistics.

    Attributes:
        total distance: The total geodesic IP distance between the TES instance
            and the task's inputs.
    """

    total_distance: float | None = None


class TesInstance(BaseModel):
    """TES instance information.

    Attributes:
        location: TES instance IP location.
        stats: TES instance statistics.
    """

    location: Location | None = None
    stats: TesStats = TesStats()


class TaskInput(BaseModel):
    """Task input information.

    Attributes:
        location: Input IP location.
    """

    location: Location | None = None


class TaskSummary(BaseModel):
    """Summary of TES instances and corresponding statistics.

    Attributs:
        inputs: A dictionary of task input URIs and corresponding information.
        tes_instances: A dictionary of TES instance URIs and corresponding
            information.
    """

    inputs: dict[str, TaskInput] = {}
    tes_instances: dict[str, TesInstance] = {}


class TaskDistributionDistance(TaskDistributionBaseClass):
    """Distance-based task distribution middleware.

    Sorts the available TES instances by the sum of minimum geodesic distances
    between a given TES instance and all of the task's inputs, in ascending
    order; instances at the same distance keep their order (that of
    `custom.tes.service_list`, followed by the discovered backends).
    Distances are calculated based on IP geolocations of the TES instance and
    the inputs.

    Attributes:
        tes_urls: TES instance best suited for TES task.
        input_uris: A list of input URIs from the incoming request.
        tes_summary: Summary of TES instances and corresponding statistics.
    """

    def __init__(self) -> None:
        """Class constructor."""
        super().__init__()
        self.task_summary: TaskSummary = TaskSummary()

    def _set_tes_urls(
        self,
        tes_urls: list[str],
        task: dict[str, Any],
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            task: Task to be modified.
        """
        self._set_tes_instances(tes_urls=tes_urls)
        self._set_task_inputs(task=task)
        self._set_locations()
        self._set_distances()
        self.tes_urls = self._rank_tes_instances()

    def _set_tes_instances(self, tes_urls: list[str]) -> None:
        """Set TES instances.

        Args:
            tes_urls: List of TES URLs.
        """
        for url in dict.fromkeys(tes_urls):
            self.task_summary.tes_instances[url] = TesInstance()

    def _set_task_inputs(self, task: dict[str, Any]) -> None:
        """Set task inputs.

        Args:
            task: Task to be modified.

        Raises:
            MiddlewareException: If no input URIs are available.
        """
        input_uris = list(
            dict.fromkeys(
                input_value.get("url")
                for input_value in task.get("inputs", [])
                if input_value.get("url") is not None
            )
        )
        if not input_uris:
            raise MiddlewareException("No input URIs available.")
        for uri in input_uris:
            self.task_summary.inputs[uri] = TaskInput()

    def _set_locations(self) -> None:
        """Set IP locations for TES instances and task inputs."""
        tes_urls = list(self.task_summary.tes_instances.keys())
        input_uris = list(self.task_summary.inputs.keys())
        uris_unique: list[str] = list(dict.fromkeys(tes_urls + input_uris))
        ips: dict[str, str] = self._get_ips(*uris_unique)
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
        locations_inputs: list[Location] = []
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

    def _rank_tes_instances(self) -> list[str]:
        """Rank TES instances by physical proximity to the task's inputs.

        Returns:
            A list of TES URIs ranked by total distance to the task's inputs,
                in ascending order.

        Raises:
            MiddlewareException: If total distance is not available for a TES
                instance.
        """
        distances: dict[str, float] = {}
        for url, instance in self.task_summary.tes_instances.items():
            if instance.stats.total_distance is None:
                raise MiddlewareException(
                    f"Total distance not available for TES instance at URL: {url}"
                )
            distances[url] = instance.stats.total_distance
        return list(dict(sorted(distances.items(), key=lambda item: item[1])).keys())

    @staticmethod
    def _get_ips(*args: str) -> dict[str, str]:
        """Get IP addresses for one or more URIs.

        Args:
            *args: URIs.

        Returns:
            Dictionary of URIs and their IP addresses.

        Raises:
            MiddlewareException: If IP address cannot be determined for a URI.
        """
        ips: dict[str, str] = {}
        for uri in args:
            try:
                ips[uri] = gethostbyname(urlparse(strip_auth(str(uri))).netloc)
            except gaierror as exc:
                raise MiddlewareException(
                    f"Could not determine IP address for URI: {uri}"
                ) from exc
        return ips

    @staticmethod
    def _get_ip_locations(*args: str) -> dict[str, Location]:
        """Get locations of IP addresses.

        Args:
            *args: IP addresses.

        Returns:
            Dictionary of unique IP addresses and their locations.

        Raises:
            MiddlewareException: If location cannot be determined for an IP.
        """
        # Each address once, in order of the arguments (requests to Nominatim
        # are not throttled, which allows one per second)
        return {ip_addr: locate_ip(ip_addr) for ip_addr in dict.fromkeys(args)}

    @staticmethod
    def _get_distances(
        node: Location,
        leaves: list[Location],
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
