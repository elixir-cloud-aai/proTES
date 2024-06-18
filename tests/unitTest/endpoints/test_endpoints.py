import unittest
from copy import deepcopy
import mongomock
from flask import Flask
from foca.models.config import (Config, MongoConfig)
from unittest.mock import patch

from pro_tes.ga4gh.tes.server import (
    ListTasks,
    GetTask,
    CancelTask,
    GetServiceInfo,
    CreateTask
)
from tests.unitTest.mock_data import (
    MONGO_CONFIG,
    CONTROLLER_CONFIG,
    SERVICE_INFO_CONFIG,
    TES_CONFIG,
    TASK_PAYLOAD_200,
    MOCK_TASKS_MINIMAL_LIST,
    MOCK_TASKS_BASIC_LIST,
    MOCK_TASKS_FULL_LIST,
    MOCK_TASK_MINIMAL1,
    MOCK_TASK_BASIC1,
    MOCK_TASK_FULL1,
    MOCK_TASK_CANCEL,
    STORE_LOGS_CONFIG,
    MIDDLEWARE_CONFIG
)


class TestEndpoints(unittest.TestCase):
    app = Flask(__name__)

    def setup(self):
        self.app.config.foca = Config(
            db=MongoConfig(**MONGO_CONFIG),
            controllers=CONTROLLER_CONFIG,
            tes=TES_CONFIG,
            serviceInfo=SERVICE_INFO_CONFIG,
            storeLogs=STORE_LOGS_CONFIG,
            middlewares=MIDDLEWARE_CONFIG
        )
        self.app.config.foca.db.dbs['taskStore'].collections[
            'tasks'
        ].client = mongomock.MongoClient().db.collection
        self.foca_config: Config = self.app.config.foca

    # @patch(
    #     'pro_tes.tasks.track_task_progress.task__track_task_progress.apply_async')
    # def test_create_task(self, mock_apply_async):
    #     self.setup()
    #     mock_apply_async.return_value = None
    #     data = deepcopy(TASK_PAYLOAD_200)
    #     with self.app.test_request_context(json=data):
    #         res = CreateTask.__wrapped__()
    #         assert res['id']

    def test_list_task_minimal(self):
        self.setup()

        for tasks in MOCK_TASKS_MINIMAL_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = ListTasks.__wrapped__(
                view='MINIMAL'
            )

        assert res['next_page_token']
        assert res['tasks']
        assert res['tasks'][0]['id']
        assert res['tasks'][0]

    def test_list_task_full(self):
        self.setup()
        with self.app.app_context():
            res = ListTasks.__wrapped__()
            assert res['tasks'] == []

        for tasks in MOCK_TASKS_FULL_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = ListTasks.__wrapped__(
                view='FULL'
            )
        assert res['next_page_token']
        assert res['tasks']
        assert res['tasks'][0]['id']
        assert res['tasks'][0]['state']
        assert res['tasks'][0]['executors']
        assert res['tasks'][0]['logs']

    def test_list_task_basic(self):
        self.setup()
        with self.app.app_context():
            res = ListTasks.__wrapped__()
            assert res['tasks'] == []

        for tasks in MOCK_TASKS_BASIC_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = ListTasks.__wrapped__(
                view='BASIC'
            )
        assert res['next_page_token']
        assert res['tasks']
        assert res['tasks'][0]['id']
        assert res['tasks'][0]['state']
        assert res['tasks'][0]['executors']

    def test_get_task_by_id_minimal(self):
        self.setup()

        for tasks in MOCK_TASKS_MINIMAL_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = GetTask.__wrapped__(
                id=MOCK_TASK_MINIMAL1['task']['id'],
                view='MINIMAL'
            )
        assert res['id'] == MOCK_TASK_MINIMAL1['task']['id']
        assert res['state'] == MOCK_TASK_MINIMAL1['task']['state']


    def test_get_task_by_id_basic(self):
        self.setup()

        for tasks in MOCK_TASKS_BASIC_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = GetTask.__wrapped__(
                id=MOCK_TASK_BASIC1['task']['id'],
                view='BASIC'
            )
        print(res)
        assert res['id'] == MOCK_TASK_BASIC1['task']['id']
        assert res['state'] == MOCK_TASK_BASIC1['task']['state']
        assert res['executors'] == MOCK_TASK_BASIC1['task']['executors']

    def test_get_task_by_id_full(self):
        self.setup()

        for tasks in MOCK_TASKS_FULL_LIST:
            self.app.config.foca.db.dbs['taskStore'].collections[
                'tasks'
            ].client.insert_one(
                tasks
            )
        with self.app.app_context():
            res = GetTask.__wrapped__(
                id=MOCK_TASK_FULL1['task']['id'],
                view='FULL'
            )
        assert res['id'] == MOCK_TASK_FULL1['task']['id']
        assert res['state'] == MOCK_TASK_FULL1['task']['state']
        assert res['executors'] == MOCK_TASK_FULL1['task']['executors']
        assert res['logs'] == MOCK_TASK_FULL1['task']['logs']

    def test_cancel_task(self):
        self.setup()
        self.app.config.foca.db.dbs['taskStore'].collections[
            'tasks'
        ].client.insert_one(
            MOCK_TASK_CANCEL
        )
        with self.app.app_context():
            res = CancelTask.__wrapped__(
                id=MOCK_TASK_CANCEL['task']['id'],
            )
        assert res == {}
