"""Module for distance-based task distribution logic."""

from copy import deepcopy
from itertools import combinations
import logging
from socket import gaierror, gethostbyname
from typing import Dict, List, Set, Tuple
from urllib.parse import urlparse

from flask import current_app
from geopy.distance import geodesic
from ip2geotools.databases.noncommercial import DbIpCity
from ip2geotools.errors import InvalidRequestError

from pro_tes.exceptions import (
    InputUriError,
    TesUriError,
    IPDistanceCalculationError
)
from pro_tes.middleware.models import (
    AccessUriCombination,
    TaskParams,
    TesDeployment,
    TesStats,
)
from pro_tes.utils.misc import remove_auth_from_url

logger = logging.getLogger(__name__)


def task_distribution(input_uri: List) -> List:
    """Task distributor.

    Args:
        input_uri: List of inputs of a TES task request

    Returns:
        A list of ranked TES instances, ordered by the minimum distance
        between the input files and each TES instance.

    Raises:
        ValueError: If no input URIs are available.
    """
    if not input_uri:
        raise ValueError("No input URIs available.")

    foca_conf = current_app.config.foca
    tes_uri: List[str] = foca_conf.tes["service_list"]
    access_uri_combination = get_uri_combination(input_uri, tes_uri)

    # get the combination of the tes ip and input ip
    ips = ip_combination(input_uri=input_uri, tes_uri=tes_uri)

    ips_unique: Dict[Set[str], List[Tuple[int, str]]] = {
        v: [] for v in ips.values()  # type: ignore
    }
    for key, value in ips.items():
        ips_unique[value].append(key)

    # Calculate distances between all IPs
    distances = calculate_distance(ips_unique, tes_uri)

    # Add distance totals
    for combination in distances:
        combination["total"] = sum(combination.values())

    # Add total distance corresponding to TES uri's in
    # access URI combination
    for index, value in enumerate(access_uri_combination.tes_deployments):
        value.stats.total_distance = distances[index]["total"]
    logger.info(f"access_uri_combination: {access_uri_combination}")

    return rank_tes_instances(access_uri_combination)


def get_uri_combination(
    input_uri: List, tes_uri: List
) -> AccessUriCombination:
    """Create a combination of input URIs and TES URIs.

    Args:
        input_uri: List of input URIs of TES request.
        tes_uri: List of TES instance.

    Returns:
        An AccessUriCombination object, which is a combination of:
            :class:`pro_tes.middleware.models.AccessUriCombination`:

    Examples:
        A AccessUriCombination object of the form like:
        {
            "task_params": {
                "input_uri": [
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test1.txt",
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test2.txt",
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test3.txt",
                ]
            },

            "tes_deployments": [
                {   "tes_uri": "https://tesk-eu.hypatia-comp.athenarc.gr",
                    "stats": {
                        "total_distance": None
                    }
                },
                {   "tes_uri": "https://csc-tesk-noauth.rahtiapp.fi",
                    "stats": {
                        "total_distance": None
                    }
                },
                {   "tes_uri": "https://tesk-na.cloud.e-infra.cz",
                    "stats": {
                        "total_distance": None
                    }
                },
        }
    """
    tes_deployment_list = [
        TesDeployment(tes_uri=uri, stats=TesStats(total_distance=None))
        for uri in tes_uri
    ]

    task_param = TaskParams(input_uris=input_uri)
    access_uri_combination = AccessUriCombination(
        task_params=task_param, tes_deployments=tes_deployment_list
    )
    return access_uri_combination


def ip_combination(input_uri: List[str], tes_uri: List[str]) -> Dict:
    """Create a pair of TES IP and Input IP.

    Args:
        input_uri: A list of input URIs for a TES task request.
        tes_uri: A list of TES instances to choose from.

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
    for index, uri in enumerate(input_uri):
        uri_no_auth = remove_auth_from_url(uri)
        try:
            obj_ip = gethostbyname(urlparse(uri_no_auth).netloc)
        except gaierror as exc:
            raise InputUriError from exc
        obj_ip_list.append(obj_ip)

    for index, uri in enumerate(tes_uri):
        uri_no_auth = remove_auth_from_url(uri)
        try:
            tes_ip = gethostbyname(urlparse(uri_no_auth).netloc)
        except gaierror as exc:
            raise TesUriError from exc
        for count, obj_ip in enumerate(obj_ip_list):
            ips[(index, count)] = (tes_ip, obj_ip)
    return ips


def ip_distance(
    *args: str,
) -> Dict[str, Dict]:
    """Compute IP distance between IP pairs.

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


def calculate_distance(
    ips_unique: Dict[Set[str], List[Tuple[int, str]]],
    tes_uri: List[str],
) -> Dict[Set[str], float]:
    """Calculate distances between all IPs.

    Args:
        ips_unique: A dictionary of unique Ips.
        tes_uri: List of TES instance.

    Returns:
        A dictionary of distances between all IP addresses.
        The keys are sets of IP addresses, and the values are the distances
        between them as floats.
    """
    distances_unique: Dict[Set[str], float] = {}
    ips_all = frozenset().union(*list(ips_unique.keys()))  # type: ignore
    try:
        distances_full = ip_distance(*ips_all)
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
    distances = [deepcopy({}) for i in range(len(tes_uri))]
    for ip_set, combination in ips_unique.items():  # type: ignore
        for combo in combination:
            distances[combo[0]][combo[1]] = distances_unique[ip_set]

    return distances


def rank_tes_instances(
    access_uri_combination: AccessUriCombination,
) -> List[str]:
    """Rank TES instances in increasing order of total distance.

    Args:
        access_uri_combination: Combination of task_params and tes_deployments.

    Returns:
        A list of TES URI in increasing order of total distance.
    """
    combination = [
        value.dict() for value in access_uri_combination.tes_deployments
    ]

    # sorting the TES uri in decreasing order of total distance
    ranked_combination = sorted(
        combination, key=lambda x: x["stats"]["total_distance"]
    )

    ranked_tes_uri = [str(value["tes_uri"]) for value in ranked_combination]
    return ranked_tes_uri
