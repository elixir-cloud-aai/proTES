"""Module for task distribution logic."""

from copy import deepcopy
import random
from urllib.parse import urlparse
from socket import gaierror, gethostbyname
from typing import Dict, List, Set, Tuple, Optional
from itertools import combinations
from ip2geotools.errors import InvalidRequestError
from ip2geotools.databases.noncommercial import DbIpCity
from geopy.distance import geodesic

from flask import current_app
import requests


def random_task_distribution() -> Optional[str]:
    """Random task distributor.

    Randomly distribute tasks across available TES instances.

    Returns:
        A randomly selected, available TES instance.
    """
    foca_conf = current_app.config.foca
    tes_uri: List[str] = deepcopy(foca_conf.tes["service_list"])
    timeout: int = foca_conf.controllers["post_task"]["timeout"]["poll"]
    while len(tes_uri) != 0:
        random_tes_uri: str = random.choice(tes_uri)
        response = requests.get(url=random_tes_uri, timeout=timeout)
        if response.status_code == 200:
            return random_tes_uri
        tes_uri.remove(random_tes_uri)
    return None


def task_distribution_by_distance(input_uri: List) -> Optional[List]:
    """Task distributor.

    Distributes task by selecting the TES instance having minimum
    distance between the input files and TES Instance.
    Returns:
        A list of ranked TES instance.
    """
    foca_conf = current_app.config.foca
    tes_uri: List[str] = deepcopy(foca_conf.tes["service_list"])
    access_uri_combination = get_uri_combination(input_uri, tes_uri)
    distances_full: Dict[str, Dict] = {}
    distances: List[Dict[str, float]] = []
    ips = {}

    # Create a pair of TES IP and Input IP for each object and each
    # URI combination
    for index in range(len(tes_uri)):
        try:
            tes_domain = urlparse(tes_uri[index]).netloc
            tes_ip = gethostbyname(tes_domain)
        except KeyError:
            continue
        except gaierror:
            continue
        for y in range(len(input_uri)):
            try:
                obj_ip = gethostbyname(urlparse(input_uri[y]).netloc)
            except gaierror:
                break
            ips[(index, y)] = (tes_ip, obj_ip)

    ips_unique: Dict[Set[str], List[Tuple[int, str]]] = {
        v: [] for v in ips.values()  # type: ignore
    }
    for k, v in ips.items():
        ips_unique[v].append(k)

    # Calculate distances between all IPs
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
                distances_unique[ip_tuple] = distances_full["distances"][ip_tuple]
            except KeyError:
                pass

    # Reshape distances keys for logging
    keys = list(distances_full["distances"].keys())
    keys = ["|".join([str(i) for i in t]) for t in keys]
    distances_full["distances"] = dict(
        zip(keys, list(distances_full["distances"].values()))
    )

    # Map distances back to each access URI combination
    distances = [deepcopy({}) for i in range(len(tes_uri))]

    for ip_set, combinations in ips_unique.items():  # type: ignore
        for combination in combinations:
            try:
                distances[combination[0]][combination[1]] = distances_unique[ip_set]
            except KeyError:
                pass

    # Add distance totals
    for combination in distances:
        combination["total"] = sum(combination.values())

    # Add total distance corresponding to each TES uri in access URI combination
    for index in range(len(distances)):
        access_uri_combination["tes_uri_list"][index]["total_distance"] = distances[index][
            "total"
        ]

    # sorting the TES uri in decreasing order of total distance
    sorted_tes_uri = sorted(access_uri_combination["tes_uri_list"], key=lambda x: x["total_distance"])

    access_uri_combination["tes_uri_list"] = sorted_tes_uri

    ranked_tes_uri = []
    for index in range(len(sorted_tes_uri)):
        ranked_tes_uri.append(sorted_tes_uri[index]["tes_uri"])
    return ranked_tes_uri


def get_uri_combination(input_uri: List, tes_uri: List) -> List:
    """Creates a combination of input uris and TES uri.

    Returns:
        A Combination of Input uris and TES uri.
        {
        "input_uri": [
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test1.txt",
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test2.txt",
                    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test3.txt",
                ],
        "tes_uri_list": [
            {
                "tes_uri": "https://tesk-eu.hypatia-comp.athenarc.gr",
                "total_distance": None
            },
            {
                "tes_uri": "https://csc-tesk-noauth.rahtiapp.fi",
                "total_distance": None
            },
            {
                "tes_uri": "https://tesk-na.cloud.e-infra.cz",
                "total_distance" : None
            },
        ]
        }
    """
    combination = {
        "input_uri": input_uri,
        "tes_uri_list": []
    }

    for index in range(len(tes_uri)):
        combination["tes_uri_list"].insert(index,
                                           {
                                               "tes_uri": tes_uri[index],
                                               "total_distance": None
                                           }
                                           )
    return combination


def ip_distance(
        *args: str,
) -> Dict[str, Dict]:
    """Computes IP distance between ip pairs.

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
    for ip in args:
        try:
            ip_locs[ip] = DbIpCity.get(ip, api_key="free")
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
