"""Mock data for Testing."""

from pydantic import AnyUrl, HttpUrl

from pro_tes.plugins.middlewares.task_distribution.models import (
    AccessUriCombination,
    TaskParams,
    TesDeployment,
    TesStats,
)

INDEX_CONFIG_TASKS = {"keys": [("task_id", 1), ("worker_id", 1)]}

COLLECTION_CONFIG_TASKS = {
    "indexes": [INDEX_CONFIG_TASKS],
}

INDEX_CONFIG_SERVICE_INFO = {"keys": [("id", 1)]}

COLLECTION_CONFIG_SERVICE_INFO = {
    "indexes": [INDEX_CONFIG_SERVICE_INFO],
}

DB_CONFIG = {
    "collections": {
        "tasks": COLLECTION_CONFIG_TASKS,
        "service_info": COLLECTION_CONFIG_SERVICE_INFO,
    },
}

MONGO_CONFIG = {
    "host": "mongodb",
    "port": 27017,
    "dbs": {
        "taskStore": DB_CONFIG,
    },
}

POST_TASK_CONFIG = {
    "db": {
        "insert_attempts": 10,
    },
    "task_id": {
        "charset": "string.ascii_uppercase + string.digits",
        "length": 6,
    },
    "timeout": {"post": 0, "poll": 2, "job": 0},
    "polling": {"wait": 3, "attempts": 100},
}

LIST_TASK_CONFIG = {"default_page_size": 256}

CELERY_CONFIG = {"monitor": {"timeout": 0.1}, "message_maxsize": 16777216}

CONTROLLER_CONFIG = {
    "post_task": POST_TASK_CONFIG,
    "list_tasks": LIST_TASK_CONFIG,
    "celery": CELERY_CONFIG,
}

SERVICE_INFO_CONFIG = {
    "doc": (
        "Proxy TES for distributing tasks across a list      of service TES"
        " instances"
    ),
    "name": "proTES",
    "storage": ["file:///path/to/local/storage"],
}

TES_CONFIG = {
    "service_list": [
        "https://csc-tesk-noauth.rahtiapp.fi",
        "https://funnel.cloud.e-infra.cz/",
        "https://tesk-eu.hypatia-comp.athenarc.gr",
        "https://tesk-na.cloud.e-infra.cz",
        "https://vm4816.kaj.pouta.csc.fi/",
    ]
}

mock_input_uri = [
    "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
]

invalid_input_uri = ["ftp://invalid.input.uri"]

mock_tes_uri = [
    "https://csc-tesk-noauth.rahtiapp.fi",
    "https://funnel.cloud.e-infra.cz/",
    "https://tesk-eu.hypatia-comp.athenarc.gr",
    "https://tesk-na.cloud.e-infra.cz",
    "https://vm4816.kaj.pouta.csc.fi/",
]

invalid_tes_uri = ["https://invalid.tes.uri"]

expected_ips = {
    (0, 0): ("193.167.189.101", "128.214.255.155"),
    (1, 0): ("147.251.253.240", "128.214.255.155"),
    (2, 0): ("62.217.122.118", "128.214.255.155"),
    (3, 0): ("147.251.253.240", "128.214.255.155"),
    (4, 0): ("86.50.228.254", "128.214.255.155"),
}

ips_unique = {
    ("193.167.189.101", "128.214.255.155"): [(0, 0)],
    ("147.251.253.240", "128.214.255.155"): [(1, 0), (3, 0)],
    ("62.217.122.118", "128.214.255.155"): [(2, 0)],
    ("86.50.228.254", "128.214.255.155"): [(4, 0)],
}

invalid_ips_unique = {
    ("invalid-ip", "invalid-ip"): [(0, 0)],
}

ips_not_unique = {
    ("193.167.189.101", "193.167.189.101"): [(0, 0)],
}

ip_distances_res = {
    "86.50.228.254": {
        "city": "Helsinki",
        "region": "Uusimaa",
        "country": "FI",
    },
    "62.217.122.118": {
        "city": "Athens (Ampelokipoi)",
        "region": "Attica",
        "country": "GR",
    },
    "128.214.255.155": {
        "city": "Helsinki",
        "region": "Uusimaa",
        "country": "FI",
    },
    "147.251.253.240": {
        "city": "Brno",
        "region": "South Moravian",
        "country": "CZ",
    },
    "193.167.189.101": {"city": "Espoo", "region": "Uusimaa", "country": "FI"},
    "distances": {
        ("86.50.228.254", "62.217.122.118"): 2468.0798992845093,
        ("62.217.122.118", "86.50.228.254"): 2468.0798992845093,
        ("86.50.228.254", "128.214.255.155"): 0.0,
        ("128.214.255.155", "86.50.228.254"): 0.0,
        ("86.50.228.254", "147.251.253.240"): 1332.2498833186016,
        ("147.251.253.240", "86.50.228.254"): 1332.2498833186016,
        ("86.50.228.254", "193.167.189.101"): 16.398441097721292,
        ("193.167.189.101", "86.50.228.254"): 16.398441097721292,
        ("62.217.122.118", "128.214.255.155"): 2468.0798992845093,
        ("128.214.255.155", "62.217.122.118"): 2468.0798992845093,
        ("62.217.122.118", "147.251.253.240"): 1370.6739529568229,
        ("147.251.253.240", "62.217.122.118"): 1370.6739529568229,
        ("62.217.122.118", "193.167.189.101"): 2471.627395977902,
        ("193.167.189.101", "62.217.122.118"): 2471.627395977902,
        ("128.214.255.155", "147.251.253.240"): 1332.2498833186016,
        ("147.251.253.240", "128.214.255.155"): 1332.2498833186016,
        ("128.214.255.155", "193.167.189.101"): 16.398441097721292,
        ("193.167.189.101", "128.214.255.155"): 16.398441097721292,
        ("147.251.253.240", "193.167.189.101"): 1328.8146841179737,
        ("193.167.189.101", "147.251.253.240"): 1328.8146841179737,
    },
}

invalid_ip_distances_res = {
    "86.50.228.254": {
        "city": "Helsinki",
        "region": "Uusimaa",
        "country": "FI",
    },
    "62.217.122.118": {
        "city": "Athens (Ampelokipoi)",
        "region": "Attica",
        "country": "GR",
    },
    "128.214.255.155": {
        "city": "Helsinki",
        "region": "Uusimaa",
        "country": "FI",
    },
    "147.251.253.240": {
        "city": "Brno",
        "region": "South Moravian",
        "country": "CZ",
    },
    "193.167.189.101": {"city": "Espoo", "region": "Uusimaa", "country": "FI"},
    "distances": {
        ("193.167.189.101", "147.251.253.240"): 1328.8146841179737,
    },
}

expected_distances = [
    {0: 16.398441097721292},
    {0: 1332.2498833186016},
    {0: 2468.0798992845093},
    {0: 1332.2498833186016},
    {0: 0.0},
]

ips_all = frozenset(
    {
        "86.50.228.254",
        "193.167.189.101",
        "62.217.122.118",
        "128.214.255.155",
        "147.251.253.240",
    }
)

invalid_ips = frozenset({"300.0.0.1", "192.168.1."})

expected_access_uri_combination = AccessUriCombination(
    task_params=TaskParams(
        input_uris=[
            AnyUrl(
                "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
                scheme="ftp",
                host="vm4466.kaj.pouta.csc.fi",
                tld="fi",
                host_type="domain",
                path="/upload/foivos/test.txt",
            )
        ]
    ),
    tes_deployments=[
        TesDeployment(
            tes_uri=HttpUrl(
                "https://csc-tesk-noauth.rahtiapp.fi",
                scheme="https",
                host="csc-tesk-noauth.rahtiapp.fi",
                tld="fi",
                host_type="domain",
            ),
            stats=TesStats(total_distance=None),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://funnel.cloud.e-infra.cz/",
                scheme="https",
                host="funnel.cloud.e-infra.cz",
                tld="cz",
                host_type="domain",
                path="/",
            ),
            stats=TesStats(total_distance=None),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://tesk-eu.hypatia-comp.athenarc.gr",
                scheme="https",
                host="tesk-eu.hypatia-comp.athenarc.gr",
                tld="gr",
                host_type="domain",
            ),
            stats=TesStats(total_distance=None),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://tesk-na.cloud.e-infra.cz",
                scheme="https",
                host="tesk-na.cloud.e-infra.cz",
                tld="cz",
                host_type="domain",
            ),
            stats=TesStats(total_distance=None),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://vm4816.kaj.pouta.csc.fi/",
                scheme="https",
                host="vm4816.kaj.pouta.csc.fi",
                tld="fi",
                host_type="domain",
                path="/",
            ),
            stats=TesStats(total_distance=None),
        ),
    ],
)

final_access_uri_combination = AccessUriCombination(
    task_params=TaskParams(
        input_uris=[
            AnyUrl(
                "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
                scheme="ftp",
                host="vm4466.kaj.pouta.csc.fi",
                tld="fi",
                host_type="domain",
                path="/upload/foivos/test.txt",
            )
        ]
    ),
    tes_deployments=[
        TesDeployment(
            tes_uri=HttpUrl(
                "https://csc-tesk-noauth.rahtiapp.fi",
                scheme="https",
                host="csc-tesk-noauth.rahtiapp.fi",
                tld="fi",
                host_type="domain",
            ),
            stats=TesStats(total_distance=16.398441097721292),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://funnel.cloud.e-infra.cz/",
                scheme="https",
                host="funnel.cloud.e-infra.cz",
                tld="cz",
                host_type="domain",
                path="/",
            ),
            stats=TesStats(total_distance=1332.2498833186016),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://tesk-eu.hypatia-comp.athenarc.gr",
                scheme="https",
                host="tesk-eu.hypatia-comp.athenarc.gr",
                tld="gr",
                host_type="domain",
            ),
            stats=TesStats(total_distance=2468.0798992845093),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://tesk-na.cloud.e-infra.cz",
                scheme="https",
                host="tesk-na.cloud.e-infra.cz",
                tld="cz",
                host_type="domain",
            ),
            stats=TesStats(total_distance=1332.2498833186016),
        ),
        TesDeployment(
            tes_uri=HttpUrl(
                "https://vm4816.kaj.pouta.csc.fi/",
                scheme="https",
                host="vm4816.kaj.pouta.csc.fi",
                tld="fi",
                host_type="domain",
                path="/",
            ),
            stats=TesStats(total_distance=0.0),
        ),
    ],
)

mock_rank_tes_instances = [
    "https://vm4816.kaj.pouta.csc.fi/",
    "https://csc-tesk-noauth.rahtiapp.fi",
    "https://funnel.cloud.e-infra.cz/",
    "https://tesk-na.cloud.e-infra.cz",
    "https://tesk-eu.hypatia-comp.athenarc.gr",
]
