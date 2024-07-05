import unittest
import mongomock
from flask import Flask
from foca.models.config import Config, MongoConfig
from flask import request

from pro_tes.plugins.middlewares.task_distribution.base import TaskDistributionBaseClass
from pro_tes.plugins.middlewares.task_distribution.random import TaskDistributionRandom
from tests.unitTest.mock_data import (
    MONGO_CONFIG,
    CONTROLLER_CONFIG,
    SERVICE_INFO_CONFIG,
    TES_CONFIG,
    STORE_LOGS_CONFIG,
    MIDDLEWARE_CONFIG,
    MOCK_REQUEST,
    MOCK_TES_URL
)


class TestBaseRandom(unittest.TestCase):
    app = Flask(__name__)

    def setUp(self):
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

    def test_base_task_distribution(self):
        self.setUp()
        with self.app.test_request_context(json=MOCK_REQUEST):
            req = TaskDistributionBaseClass().apply_middleware(request=request)
            assert len(req.json["tes_urls"]) == len(MOCK_TES_URL)

    def test_random_task_distribution(self):
        self.setUp()
        with self.app.test_request_context(json=MOCK_REQUEST):
            req = TaskDistributionRandom().apply_middleware(request=request)
            assert len(req.json["tes_urls"]) == len(MOCK_TES_URL)
