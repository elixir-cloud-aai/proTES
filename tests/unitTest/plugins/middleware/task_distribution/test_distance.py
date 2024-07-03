import unittest
import mongomock
import pytest
from flask import Flask
from foca.models.config import Config, MongoConfig
from flask import request
import logging

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution.distance import (
    TaskDistributionDistance,
)
from tests.unitTest.mock_data import (
    MONGO_CONFIG,
    CONTROLLER_CONFIG,
    SERVICE_INFO_CONFIG,
    TES_CONFIG,
    STORE_LOGS_CONFIG,
    MIDDLEWARE_CONFIG,
)

logger = logging.getLogger(__name__)

mock_request = {
    "description": "sample task",
    "tags": {"WORKFLOW_ID": "cwl-01234", "PROJECT_GROUP": "alice-lab"},
    "inputs": [
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
    ],
    "executors": [{"image": "alpine", "command": ["echo", "hello"]}],
}

mock_tes_url = [
    "https://csc-tesk-noauth.rahtiapp.fi",
    "https://funnel.cloud.e-infra.cz/",
    "https://tesk-eu.hypatia-comp.athenarc.gr",
    "https://tesk-na.cloud.e-infra.cz",
    "https://vm4816.kaj.pouta.csc.fi/",
]

req_json = {
    "description": "sample task",
    "executors": [{"command": ["echo", "hello"], "image": "alpine"}],
    "inputs": [
        {
            "description": "cwl_input:input",
            "name": "input",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
        },
        {
            "description": "cwl_input:input",
            "name": "input",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
        },
        {
            "description": "cwl_input:input",
            "name": "input",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
            "url": "ftp://vm4466.kaj.pouta.csc.fi/upload/foivos/test.txt",
        },
    ],
    "tags": {"PROJECT_GROUP": "alice-lab", "WORKFLOW_ID": "cwl-01234"},
    "tes_urls": [
        "https://vm4816.kaj.pouta.csc.fi/",
        "https://csc-tesk-noauth.rahtiapp.fi",
        "https://funnel.cloud.e-infra.cz/",
        "https://tesk-na.cloud.e-infra.cz",
        "https://tesk-eu.hypatia-comp.athenarc.gr",
    ],
}

data_no_input_uri = {
    "description": "sample task",
    "executors": [{"command": ["echo", "hello"], "image": "alpine"}],
}

invalid_input_uri_data = {
    "description": "sample task",
    "tags": {"WORKFLOW_ID": "cwl-01234", "PROJECT_GROUP": "alice-lab"},
    "inputs": [
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
        {
            "name": "input",
            "description": "cwl_input:input",
            "url": "ftp://upload/foivos/test.txt",
            "path": "/var/lib/cwl/stgc957b135-7bd5-4249-9c37-265363c1e699/test.txt",
            "type": "FILE",
        },
    ],
    "executors": [{"image": "alpine", "command": ["echo", "hello"]}],
}

ranked_tes_instance = [
        "https://vm4816.kaj.pouta.csc.fi/",
        "https://csc-tesk-noauth.rahtiapp.fi",
        "https://funnel.cloud.e-infra.cz/",
        "https://tesk-na.cloud.e-infra.cz",
        "https://tesk-eu.hypatia-comp.athenarc.gr",
    ]


class TestDistance(unittest.TestCase):
    app = Flask(__name__)

    def setup(self):
        self.app.config.foca = Config(
            db=MongoConfig(**MONGO_CONFIG),
            controllers=CONTROLLER_CONFIG,
            tes=TES_CONFIG,
            serviceInfo=SERVICE_INFO_CONFIG,
            storeLogs=STORE_LOGS_CONFIG,
            middlewares=MIDDLEWARE_CONFIG,
        )
        self.app.config.foca.db.dbs["taskStore"].collections[
            "tasks"
        ].client = mongomock.MongoClient().db.collection
        self.foca_config: Config = self.app.config.foca
        self.tes_url = mock_tes_url
        self.request = mock_request

    def test_distance_middleware(self):
        self.setup()
        with self.app.test_request_context(json=mock_request):
            req = TaskDistributionDistance().apply_middleware(request=request)
            assert req.json['tes_urls'] == ranked_tes_instance

    def test_empty_request_payload(self):
        self.setup()
        with self.app.test_request_context(json=None):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance()._set_task_inputs(request=request)

    def test_no_input_uri(self):
        self.setup()
        with self.app.test_request_context(json=data_no_input_uri):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance()._set_task_inputs(request=request)

    def test_invalid_tes_uri(self):
        self.setup()
        with self.app.test_request_context(json=invalid_input_uri_data):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance().apply_middleware(request=request)
