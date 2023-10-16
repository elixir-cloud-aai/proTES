"""Module for distance-based task distribution logic."""

from copy import deepcopy
from itertools import combinations
import logging
from socket import gaierror, gethostbyname
from typing import Optional
from urllib.parse import urlparse

import flask
from geopy.distance import geodesic
from ip2geotools.databases.noncommercial import DbIpCity
from ip2geotools.errors import InvalidRequestError
from pydantic import (  # pragma pylint: disable=no-name-in-module
    AnyUrl,
    BaseModel,
    HttpUrl,
)

from pro_tes.exceptions import (
    InputUriError,
    IPDistanceCalculationError,
    MiddlewareException,
    TesUriError,
)
from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)
from pro_tes.utils.misc import remove_auth_from_url

logger = logging.getLogger(__name__)

# pragma pylint: disable=too-few-public-methods


class TaskParams(BaseModel):
    """Combination of task parameters.

    Attributes:
        input_uris: The URIs of a task's inputs.
    """

    input_uris: list[AnyUrl]


class TesStats(BaseModel):
    """Combination of TES statistics.

    Attributes:
        total distance: The tota geodesic IP distance between the a TES
            instance and all of a task's inputs.
    """

    total_distance: Optional[float] = None


class TesDeployment(BaseModel):
    """Combination of a TES instance's URI and its statistics.

    Attributes:
        uri: The URI of a TES instance.
        stats: An instance of `TesStats`.
    """

    uri: HttpUrl
    stats: TesStats


class AccessUriCombination(BaseModel):
    """Combination of input_uri of the TES task and the TES instances.

    Attributs:
        task_param: A `TaskParams` object.
        tes_deployments: A list of `TesDeployment` objects.
    """

    task_params: TaskParams
    tes_deployments: list[TesDeployment]


class TaskDistributionDistance(TaskDistributionBaseClass):
    """Distance-based task distribution middleware.

    Sorts the available TES instances by the sum of minimum geodesic distances
    between a given TES instance and all of the task'sinputs, in ascending
    order. Distances are calculated based on IP geolocations of the TES
    instance and the inputs.

    Attributes:
        tes_uris: TES instance best suited for TES task.
        input_uris: A list of input URIs from the incoming request.
    """

    def __init__(self) -> None:
        """Class constructor."""
        super().__init__()
        self.input_uris: list[str] = []

    def _set_tes_uris(
        self,
        tes_uris: list[str],
        request: flask.Request,
    ) -> None:
        """Set TES URIs.

        Args:
            tes_uris: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_uris = tes_uris
        self._set_input_uris(request=request)
        combinations = self._get_uri_combinations()
        ip_combos = self._get_ip_combinations()

        # ips_unique: dict[set[str], list[tuple[int, str]]] = {
        #     value: [] for value in ip_combos.values()
        # }
        # for key, value in ip_combos.items():
        #     ips_unique[value].append(key)

        # # Calculate distances between all IPs
        # distances = self._get_distances(ips_unique)

        # # Add total distance corresponding to TES uri's in
        # # access URI combination
        # for index, value in enumerate(combinations.tes_deployments):
        #     value.stats.total_distance = distances[index]["total"]
        # logger.info(f"access_uri_combination: {combinations}")

        self.tes_uris = self._rank_tes_instances(combinations)

    def _set_input_uris(self, request: flask.Request) -> None:
        """Set input URIs.

        Args:
            request: Request object to be modified.

        Raises:
            MiddlewareException: If not input URIs are available in the request
                payload.
        """
        assert request.json is not None
        if "inputs" in request.json.keys():
            for index in range(len(request.json["inputs"])):
                if "url" in request.json["inputs"][index].keys():
                    self.input_uris.append(
                        request.json["inputs"][index]["url"]
                    )
        if not self.input_uris:
            raise MiddlewareException("No input URIs available.")

    def _get_uri_combinations(self) -> AccessUriCombination:
        """Create all combinations of input and TES URIs.

        Returns:
            An `AccessUriCombination` object.
        """
        tes_deployment_list = [
            TesDeployment(uri=uri, stats=TesStats(total_distance=None))
            for uri in self.tes_uris
        ]
        task_param = TaskParams(input_uris=self.input_uris)
        access_uri_combination = AccessUriCombination(
            task_params=task_param, tes_deployments=tes_deployment_list
        )
        return access_uri_combination

    def _get_ip_combinations(self) -> dict[tuple[int, int], tuple[str, str]]:
        """Create combinations of TES and input IP addresses.

        Returns:
            A dictionary where the keys are tuples representing the combination
            of TES instance and input URI, and the values are tuples containing
            the IP addresses of the TES instance and input URI.

        Example:
            {
                (0, 0): ('10.0.0.1', '192.168.0.1'),
                (0, 1): ('10.0.0.1', '192.168.0.2'),
                (1, 0): ('10.0.0.2', '192.168.0.1'),
                (1, 1): ('10.0.0.2', '192.168.0.2')
            }
        """
        ips = {}

        obj_ip_list = []
        for index, uri in enumerate(self.input_uris):
            uri_no_auth = remove_auth_from_url(uri)
            try:
                obj_ip = gethostbyname(urlparse(uri_no_auth).netloc)
            except gaierror as exc:
                raise InputUriError from exc
            obj_ip_list.append(obj_ip)

        for index, uri in enumerate(self.tes_uris):
            uri_no_auth = remove_auth_from_url(uri)
            try:
                tes_ip = gethostbyname(urlparse(uri_no_auth).netloc)
            except gaierror as exc:
                raise TesUriError from exc
            for count, obj_ip in enumerate(obj_ip_list):
                ips[(index, count)] = (tes_ip, obj_ip)
        return ips

    def _get_distances(
        self,
        ips_unique: dict[set[str], list[tuple[int, str]]],
    ) -> dict[set[str], float]:
        """Calculate distances between all IPs.

        Args:
            ips_unique: A dictionary of unique Ips.

        Returns:
            A dictionary of distances between all IP addresses.
            The keys are sets of IP addresses, and the values are the distances
            between them as floats.
        """
        distances_unique: dict[set[str], float] = {}
        ips_all = frozenset().union(*list(ips_unique.keys()))  # type: ignore
        try:
            distances_full = self._get_distances_helper(*ips_all)
        except ValueError as exc:
            raise IPDistanceCalculationError from exc

        for ip_tuple in ips_unique.keys():
            if len(set(ip_tuple)) == 1:
                distances_unique[ip_tuple] = 0
            else:
                try:
                    distances_unique[ip_tuple] = distances_full["distances"][
                        ip_tuple
                    ]
                except KeyError as exc:
                    raise KeyError(
                        f"Distances not found for IP addresses: {ip_tuple}"
                    ) from exc

        # Reshape distances keys for logging
        keys = list(distances_full["distances"].keys())
        keys = ["|".join([str(i) for i in t]) for t in keys]
        distances_full["distances"] = dict(
            zip(keys, list(distances_full["distances"].values()))
        )

        # Map distances back to each combination
        distances = [deepcopy({}) for i in range(len(self.tes_uris))]
        for ip_set, combination in ips_unique.items():  # type: ignore
            for combo in combination:
                distances[combo[0]][combo[1]] = distances_unique[ip_set]

        for combination in distances:
            combination["total"] = sum(combination.values())

        return distances

    def _get_distances_helper(
        self,
        *args: str,
    ) -> dict[str, dict]:
        """Compute IP distance across IP pairs.

        Args:
            *args: IP addresses of the form '8.8.8.8' without schema and
            suffixes.

        Returns:
            A dictionary with a key for each IP address, pointing to a
            dictionary containing city, region and country information for the
            IP address, as well as a key "distances" pointing to a dictionary
            indicating the distances, in kilometers, between all pairs of IPs,
            with the tuple of IPs as the keys. IPs that cannot be located are
            skipped from the resulting dictionary.

        Raises:
             ValueError: No args were passed.
        """
        if not args:
            raise ValueError("Expected at least one URI or IP address.")

        # Locate IPs
        ip_locs = {}
        for ips in args:
            try:
                ip_locs[ips] = DbIpCity.get(ips, api_key="free")
            except InvalidRequestError:
                pass

        # Compute distances
        dist = {}
        for keys in combinations(ip_locs.keys(), r=2):
            dist[(keys[0], keys[1])] = geodesic(
                (ip_locs[keys[0]].latitude, ip_locs[keys[0]].longitude),
                (ip_locs[keys[1]].latitude, ip_locs[keys[1]].longitude),
            ).km
            dist[(keys[1], keys[0])] = dist[(keys[0], keys[1])]

        # Prepare results
        res = {}
        for key, value in ip_locs.items():
            res[key] = {
                "city": value.city,
                "region": value.region,
                "country": value.country,
            }
        res["distances"] = dist

        return res

    @staticmethod
    def _rank_tes_instances(
        combinations: AccessUriCombination,
    ) -> list[str]:
        """Rank TES instances by physical proximity to the task's inputs.

        Args:
            access_uri_combination: An `AccessUriCombination` object.

        Returns:
            A list of TES URIs ranked by total distance to the task's inputs,
                in ascending order.
        """
        combos = [value.dict() for value in combinations.tes_deployments]
        ranked = sorted(combos, key=lambda x: x["stats"]["total_distance"])
        return [str(value["tes_uri"]) for value in ranked]
