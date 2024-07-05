import unittest
import mongomock
import pytest
from flask import Flask
from foca.models.config import Config, MongoConfig
from flask import request

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
    MOCK_REQUEST,
    MOCK_RANKED_TES_LIST,
    MOCK_TES_URL,
    MOCK_DATA_NO_INPUT,
    MOCK_INVALID_INPUT
)


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
        self.tes_url = MOCK_TES_URL
        self.request = MOCK_REQUEST

    def test_distance_middleware(self):
        self.setup()
        with self.app.test_request_context(json=MOCK_REQUEST):
            req = TaskDistributionDistance().apply_middleware(request=request)
            assert req.json['tes_urls'] == MOCK_RANKED_TES_LIST

    def test_empty_request_payload(self):
        self.setup()
        with self.app.test_request_context(json=None):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance()._set_task_inputs(request=request)

    def test_no_input_uri(self):
        self.setup()
        with self.app.test_request_context(json=MOCK_DATA_NO_INPUT):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance()._set_task_inputs(request=request)

    def test_invalid_tes_uri(self):
        self.setup()
        with self.app.test_request_context(json=MOCK_INVALID_INPUT):
            with pytest.raises(MiddlewareException):
                TaskDistributionDistance().apply_middleware(request=request)
