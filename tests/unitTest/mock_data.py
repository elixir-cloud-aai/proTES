"""Mock data for Testing."""

DB = "taskStore"

INDEX_CONFIG_TASKS = {
    'keys': [('task_id', 1), ('worker_id', 1)]
}

INDEX_CONFIG_SERVICE_INFO = {
    'keys': [('id', 1)]
}

COLLECTION_CONFIG_TASKS = {
    'indexes': [INDEX_CONFIG_TASKS],
}

COLLECTION_CONFIG_SERVICE_INFO = {
    'indexes': [INDEX_CONFIG_SERVICE_INFO],
}

DB_CONFIG = {
    'collections': {
        'tasks': COLLECTION_CONFIG_TASKS,
        'service_info': COLLECTION_CONFIG_SERVICE_INFO,
    },
}

POST_TASK_CONFIG = {
    "db": {
        "insert_attempts": 10,
    },
    "task_id": {
        "charset": "string.ascii_uppercase + string.digits",
        "length": 6
    },
    "timeout": {
        "post": 0,
        "poll": 2,
        "job": 0
    },
    "polling": {
        "wait": 3,
        "attempts": 100
    },
}

LIST_TASK_CONFIG = {
    'default_page_size': 5
}

CELERY_CONFIG = {
    "monitor": {
        "timeout": 0.1
    },
    "message_maxsize": 16777216
}

CONTROLLER_CONFIG = {
    'post_task': POST_TASK_CONFIG,
    'list_tasks': LIST_TASK_CONFIG,
    'celery': CELERY_CONFIG
}

SERVICE_INFO_CONFIG = {
    'doc': "Proxy TES for distributing tasks across a list \
     of service TES instances",
    'name': "proTES",
    'storage': [
        "file:///path/to/local/storage"
    ]
}

TES_CONFIG = {
    "service_list": [
        "https://tesk-eu.hypatia-comp.athenarc.gr/",
        "https://csc-tesk-noauth.rahtiapp.fi"
    ]
}

MONGO_CONFIG = {
    'host': 'mongodb',
    'port': 27017,
    'dbs': {
        'taskStore': DB_CONFIG,
    },
}

STORE_LOGS_CONFIG = {
    "execution_trace": True
}

MOCK_HEADERS = {
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

TASK_PAYLOAD_200 = {
    "executors": [
        {
            "image": "alpine",
            "command": [
                "echo",
                "hello"
            ]
        }
    ]
}

MOCK_TASK_MINIMAL1 = {
    'task': {
        "id": "task-53ef00fd",
        "state": "COMPLETE"
    }
}

MOCK_TASK_MINIMAL2 = {
    'task': {
        "id": "task-27e61564",
        "state": "COMPLETE"
    }
}

MOCK_TASK_MINIMAL3 = {
    'task': {
        "id": "task-c2cfdc1b",
        "state": "CANCELED"
    }
}

MOCK_TASK_BASIC1 = {
    'task': {
        "executors": [
            {
                "command": [
                    "echo",
                    "hello"
                ],
                "image": "alpine"
            }
        ],
        "id": "task-6332518b",
        "state": "COMPLETE"
    }
}

MOCK_TASK_BASIC2 = {
    'task': {
        "executors": [
            {
                "command": [
                    "echo",
                    "hello"
                ],
                "image": "alpine"
            }
        ],
        "id": "task-2d50216c",
        "state": "UNKNOWN"
    }
}

MOCK_TASK_FULL1 = {
    "task": {
        "creation_time": "2022-08-25T06:36:21Z",
        "executors": [
            {
                "command": [
                    "echo",
                    "hello"
                ],
                "image": "alpine"
            }
        ],
        "id": "task-d5d38b12",
        "logs": [
            {
                "end_time": "2022-08-25T06:36:39Z",
                "logs": [
                    {
                        "end_time": "2022-08-25T06:36:32Z",
                        "exit_code": 0,
                        "start_time": "2022-08-25T06:36:24Z",
                        "stdout": ""
                    }
                ],
                "metadata": {
                    "USER_ID": "anonymousUser"
                },
                "start_time": "2022-08-25T06:36:21Z"
            }
        ],
        "state": "COMPLETE"
    },
}

MOCK_TASK_FULL2 = {
    "task": {
        "creation_time": "2022-08-25T05:50:23Z",
        "executors": [
            {
                "command": [
                    "echo",
                    "hello"
                ],
                "image": "alpine"
            }
        ],
        "id": "task-d43ac869",
        "logs": [
            {
                "end_time": "2022-08-25T05:50:44Z",
                "logs": [
                    {
                        "end_time": "2022-08-25T05:50:40Z",
                        "exit_code": 0,
                        "start_time": "2022-08-25T05:50:33Z",
                        "stdout": ""
                    }
                ],
                "metadata": {
                    "USER_ID": "anonymousUser"
                },
                "start_time": "2022-08-25T05:50:24Z"
            }
        ],
        "state": "CANCELED"
    },
}

MOCK_TASK_CANCEL = {
    "worker_id": "a0604c66-acb4-4674-ae1b-db585826241c",
    "task": {
        "executors": [
            {
                "image": "alpine",
                "command": [
                    "echo",
                    "hello"
                ]
            }
        ],
        "tes_uri": [
            "https://csc-tesk.c03.k8s-popup.csc.fi/",
            "https://tes.tsi.ebi.ac.uk/",
            "https://tes-dev.tsi.ebi.ac.uk/swagger-ui.html"
        ],
        "id": "KKJ4R6",
        "state": "SYSTEM_ERROR"
    },
    "tes_endpoint": {
        "host": "https://csc-tesk-noauth.rahtiapp.fi",
        "base_path": "",
        "task_id": "KKJ4R6"
    }
}

MOCK_TASKS_MINIMAL_LIST = [
    MOCK_TASK_MINIMAL1,
    MOCK_TASK_MINIMAL2,
    MOCK_TASK_MINIMAL3
]

MOCK_TASKS_BASIC_LIST = [
    MOCK_TASK_BASIC1,
    MOCK_TASK_BASIC2
]

MOCK_TASKS_FULL_LIST = [
    MOCK_TASK_FULL1,
    MOCK_TASK_FULL2
]
