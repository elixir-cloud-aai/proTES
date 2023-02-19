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

    Distributes task by selecting the TES instance having minimum
    distance between the input files and TES Instance.

    Args:
        input_uri: List of inputs of a TES task request

    Returns:
        A list of ranked TES instance.
    """
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
    """Create a combination of input uris and tes uri.

    Args:
        input_uri: List of input uris of TES request.
        tes_uri: List of TES instance.
    Returns:
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
        input_uri: List of input uris of TES request.
        tes_uri: List of TES instance.

    Returns:
        A dictionary of combination of tes ip with all the input ips.
    """
    ips = {}

    obj_ip_list = []
    for index, uri in enumerate(input_uri):
        uri_no_auth = remove_auth_from_url(uri)
        try:
            obj_ip = gethostbyname(urlparse(uri_no_auth).netloc)
        except gaierror:
            break
        obj_ip_list.append(obj_ip)

    for index, uri in enumerate(tes_uri):
        uri_no_auth = remove_auth_from_url(uri)
        try:
            tes_ip = gethostbyname(urlparse(uri_no_auth).netloc)
        except (KeyError, gaierror):
            continue
        for count, obj_ip in enumerate(obj_ip_list):
            ips[(index, count)] = (tes_ip, obj_ip)
    return ips


def ip_distance(
    *args: str,
) -> Dict[str, Dict]:
    """Compute ip distance between ip pairs.

    :param *args: IP addresses of the form '8.8.8.8' without schema and
            suffixes.

    :return: A dictionary with a key for each IP address, pointing to a
            dictionary containing city, region and country information for the
            IP address, as well as a key "distances" pointing to a dictionary
            indicating the distances, in kilometers, between all pairs of IPs,
            with the tuple of IPs as the keys. IPs that cannot be located are
            skipped from the resulting dictionary.

    :raises ValueError: No args were passed.
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
    ips_unique: Dict[Set[str], List[Tuple[int, str]]], tes_uri: List[str]
) -> Dict[Set[str], float]:
    """Calculate distances between all IPs.

    Args:
        ips_unique: A dictionary of unique ips.
        tes_uri: List of TES instance.

    Returns:
        A dictionary of distances between all ips.
    """
    distances_unique: Dict[Set[str], float] = {}
    ips_all = frozenset().union(*list(ips_unique.keys()))  # type: ignore
    try:
        distances_full = ip_distance(*ips_all)
    except ValueError:
        pass

    for ip_tuple in ips_unique.keys():
        if len(set(ip_tuple)) == 1:
            distances_unique[ip_tuple] = 0
        else:
            try:
                distances_unique[ip_tuple] = distances_full["distances"][
                    ip_tuple
                ]
            except KeyError:
                pass

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
            try:
                distances[combo[0]][combo[1]] = distances_unique[ip_set]
            except KeyError:
                pass

    return distances


def rank_tes_instances(
    access_uri_combination: AccessUriCombination,
) -> List[str]:
    """Rank the tes instance based on the total distance.

    Args:
        access_uri_combination: Combination of task_params and tes_deployments.

    Returns:
        A list of tes uri in increasing order of total distance.
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
