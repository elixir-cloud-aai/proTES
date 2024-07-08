import unittest
import mongomock

from flask import Flask
from foca.models.config import Config, MongoConfig
from flask import request

from pro_tes.middleware.middleware_handler import MiddlewareHandler
from pro_tes.plugins.middlewares.task_distribution.base import \
    TaskDistributionBaseClass
from pro_tes.plugins.middlewares.task_distribution.distance import \
    TaskDistributionDistance
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


class TestMiddlewareHandler(unittest.TestCase):
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
        self.tes_url = MOCK_TES_URL
        self.request = MOCK_REQUEST

    def test_set_middleware(self):
        self.setUp()
        with self.app.app_context():
            mw_handler = MiddlewareHandler()
            mw_handler.set_middlewares(paths=self.app.config.foca.middlewares)
            for middleware_class in mw_handler.middlewares:
                assert all(issubclass(cls, TaskDistributionBaseClass)
                           for cls in middleware_class)

    def test_apply_middleware(self):
        self.setUp()
        with self.app.test_request_context(json=MOCK_REQUEST):
            mw_handler = MiddlewareHandler()
            mw_handler.set_middlewares(paths=self.app.config.foca.middlewares)
            req = mw_handler.apply_middlewares(request=request)
            assert req.json
            assert req.json['tes_urls']

    def test_import_middleware(self):
        self.setUp()
        with self.app.app_context():
            mw_handler = MiddlewareHandler()
            mw_class = mw_handler._import_middleware_class(import_path="pro_tes.plugins.middlewares.task_distribution.distance.TaskDistributionDistance")
            mw_instance = mw_class()
            assert isinstance(mw_instance, TaskDistributionDistance)

    


