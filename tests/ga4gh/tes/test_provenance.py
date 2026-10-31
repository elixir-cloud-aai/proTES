"""Tests of the provenance of tasks (#334), with fake plugins and backends.

The provenance of a task's pipeline run is stored with the task
(`provenance`), by FOCA's `ProvenanceStore`: the prospective part when the
task is created, the retrospective part (attempts, final reason) once its
backend is known. Plugin builds and admin-defined pipelines are stored once,
by hash; records of failed runs only with `plugins.provenance.record_failed`.
"""

from datetime import datetime
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_custom_config
from foca.plugins import GatewayError, ProvenanceStore
from foca.plugins.backends import BackendListConfig
from foca.plugins.engine import PipelineRun
from foca.plugins.provenance import FAILURE_INDEXES
from pymongo import MongoClient
import pytest
import tes
import yaml

import pro_tes
from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import BackendOutcomeUnknown
from pro_tes.ga4gh.tes import task_runs as task_runs_module
from pro_tes.ga4gh.tes.models import DbDocument
from pro_tes.ga4gh.tes.task_runs import TaskRuns
from pro_tes.utils.db import (
    FAILED_RUNS_COLLECTION,
    PIPELINES_COLLECTION,
    PLUGIN_BUILDS_COLLECTION,
    TASK_DB,
    DbDocumentConnector,
)
from tests.conftest import DB
from tests.ga4gh.tes.conftest import (
    BACKENDS,
    BASE_PATH,
    TASK,
    FakeBackend,
    PluginClient,
    relayed,
    stored,
)
from tests.ga4gh.tes.test_plugins import Reject, Rename, step

# Base config of the app
CONFIG = Path(pro_tes.__file__).parent / "config.yaml"


def test_collections_config() -> None:
    """The collections of the provenance are declared in `config.yaml`.

    Failed runs expire after FOCA's default retention (TTL index).
    """
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    collections = config["db"]["dbs"][TASK_DB]["collections"]
    assert collections[PLUGIN_BUILDS_COLLECTION] == {}
    assert collections[PIPELINES_COLLECTION] == {}
    assert collections[FAILED_RUNS_COLLECTION] == {"indexes": FAILURE_INDEXES}


def test_failed_runs_index(
    app: FlaskApp, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Records of failed runs expire (TTL index on `failed_at`)."""
    indexes = mongo_client[DB][FAILED_RUNS_COLLECTION].index_information()
    (ttl,) = (index for index in indexes.values() if "expireAfterSeconds" in index)
    assert ttl["key"] == [("failed_at", 1)]
    assert (
        ttl["expireAfterSeconds"] == FAILURE_INDEXES[0]["options"]["expireAfterSeconds"]
    )


def test_provenance(task_id: str, mongo_client: MongoClient[dict[str, Any]]) -> None:
    """Tasks have the provenance of their run; the pipeline is stored once."""
    provenance = stored(mongo_client, task_id)["provenance"]
    assert provenance["engine"]["app"]["name"] == "proTES"
    pipeline = provenance["pipeline"]
    assert pipeline["source"] == "default"
    assert "resolved" not in pipeline
    (document,) = mongo_client[DB][PIPELINES_COLLECTION].find()
    assert document["_id"] == pipeline["resolved_hash"]
    assert document["resolved"][0]["id"] == "route"
    (attempt,) = provenance["attempts"]
    assert attempt["backend"] == BACKENDS[0]
    assert attempt["outcome"] == "accepted"
    assert attempt["remote_id"] == "remote-1"
    assert provenance["final_reason"] is None
    assert "stored" not in provenance


def test_provenance_failover(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Attempts of backends that did not accept the task are recorded."""
    backend.failing.add(BACKENDS[0])
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    provenance = stored(mongo_client, response.get_json()["id"])["provenance"]
    assert [
        (attempt["backend"], attempt["outcome"]) for attempt in provenance["attempts"]
    ] == [(BACKENDS[0], "not_accepted"), (BACKENDS[1], "accepted")]
    # the same pipeline, stored once
    assert mongo_client[DB][PIPELINES_COLLECTION].count_documents({}) == 1


def test_provenance_plugins(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """The builds of the plugins that ran are stored once, by hash."""
    client = with_plugins({"test.rename": Rename}, pipeline=[step("test.rename")])
    for _ in range(2):
        assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    documents = list(mongo_client[DB]["tasks"].find())
    assert len(documents) == 2
    plugins = documents[0]["provenance"]["plugins"]
    assert list(plugins.values()) == ["test.rename"]
    (build,) = mongo_client[DB][PLUGIN_BUILDS_COLLECTION].find()
    assert build["_id"] in plugins
    (execution,) = documents[0]["provenance"]["executions"]
    assert execution["phase"] == "request"
    assert execution["changes"]["paths"] == ["/payload/name"]


def test_provenance_outcome_unknown(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks that a backend may have accepted have their provenance, too."""

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Fail as if the response timed out."""
        raise BackendOutcomeUnknown("timed out")

    monkeypatch.setattr(backend, "create_task", create_task)
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 502
    (document,) = mongo_client[DB]["tasks"].find()
    provenance = document["provenance"]
    (attempt,) = provenance["attempts"]
    assert attempt["outcome"] == "unknown"
    assert provenance["final_reason"] == "outcome_unknown"


@pytest.mark.parametrize("record_failed", [False, True])
def test_failed_run_rejected(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    record_failed: bool,
) -> None:
    """Runs that a plugin rejected are recorded only with `record_failed`."""
    client = with_plugins(
        {"test.reject": Reject},
        pipeline=[step("test.reject")],
        provenance={"record_failed": record_failed},
    )
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 403
    assert not relayed(backend)
    failures = list(mongo_client[DB][FAILED_RUNS_COLLECTION].find())
    if not record_failed:
        assert failures == []
        return
    (failure,) = failures
    assert isinstance(failure["failed_at"], datetime)
    assert failure["error"] == {
        "type": "PipelineRejected",
        "status": 403,
        "slug": "rejected",
    }
    assert failure["attempts"] == []


def test_failed_run_no_backend(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Runs whose task no backend accepted are recorded, with the attempts.

    The task is not kept.
    """
    backend.failing.update(BACKENDS)
    client = with_plugins({}, provenance={"record_failed": True})
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 503
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    (failure,) = mongo_client[DB][FAILED_RUNS_COLLECTION].find()
    assert failure["final_reason"] == "no_backend"
    assert [attempt["outcome"] for attempt in failure["attempts"]] == [
        "not_accepted",
        "not_accepted",
    ]


def test_failed_run_no_candidates(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Runs without candidates are recorded, without attempts."""
    client = with_plugins({}, provenance={"record_failed": True})
    with client.application.app_context():
        get_custom_config(CustomConfig).tes.backends = BackendListConfig([])
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 503
    (failure,) = mongo_client[DB][FAILED_RUNS_COLLECTION].find()
    assert failure["final_reason"] == "no_backend"
    assert failure["error"]["type"] == "NoBackendAvailable"
    assert failure["attempts"] == []


def test_provenance_not_updated(
    app: FlaskApp, caplog: pytest.LogCaptureFixture
) -> None:
    """Errors of updating the provenance are logged, not raised.

    The backend accepted the task already.
    """
    with app.app.app_context():
        task_runs = TaskRuns()
    task_runs.db_client = MagicMock()
    task_runs.db_client.update_one.side_effect = RuntimeError("database down")
    task_runs.provenance = MagicMock()
    run = MagicMock()
    with caplog.at_level(logging.ERROR):
        task_runs._update_provenance(run=run, worker_id="worker-1")
    assert "Provenance of task with worker ID 'worker-1' not updated" in caplog.text


def test_provenance_not_loaded(
    app: FlaskApp, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Task views and the task tracking don't load the provenance."""
    with app.app.app_context():
        task_runs = TaskRuns()
    for view in ("BASIC", "FULL"):
        assert task_runs._set_projection(view=view)["provenance"] is False
    document = stored(mongo_client, task_id)
    connector = DbDocumentConnector(
        collection=mongo_client[DB]["tasks"], worker_id=document["worker_id"]
    )
    assert connector.get_document().provenance is None
    assert DbDocument(**document).provenance == document["provenance"]


def test_provenance_response_failed(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    tracked: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors of the `response` phase keep the task, with its provenance.

    The task is tracked; the run is not recorded as failed: its task runs at
    the backend.
    """

    def respond(self: PipelineRun, *args: Any, **kwargs: Any) -> None:
        """Fail the `response` phase."""
        raise GatewayError("response failed", run=self)

    client = with_plugins({}, provenance={"record_failed": True})
    monkeypatch.setattr(PipelineRun, "respond", respond)
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 500
    (document,) = mongo_client[DB]["tasks"].find()
    (attempt,) = document["provenance"]["attempts"]
    assert attempt["outcome"] == "accepted"
    assert len(tracked) == 1
    assert mongo_client[DB][FAILED_RUNS_COLLECTION].count_documents({}) == 0


def test_provenance_not_stored(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks are created if plugin builds can't be stored; records keep them."""

    class Unwritable(ProvenanceStore):
        """Store whose collection of plugin builds can't be written."""

        def __init__(self, **kwargs: Any) -> None:
            """Create the store, with an unwritable collection of builds."""
            super().__init__(**kwargs)
            self.plugin_builds = MagicMock()
            self.plugin_builds.update_one.side_effect = RuntimeError("down")

    monkeypatch.setattr(task_runs_module, "ProvenanceStore", Unwritable)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    provenance = stored(mongo_client, response.get_json()["id"])["provenance"]
    assert provenance["stored"] is False
    assert set(provenance["builds"]) == set(provenance["plugins"])
    assert mongo_client[DB][PLUGIN_BUILDS_COLLECTION].count_documents({}) == 0


def test_provenance_collections_lazy(
    client: FlaskClient, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only `POST /tasks` uses the collections of the provenance."""
    get_collection = task_runs_module.get_collection

    def tasks_only(db: str, collection: str) -> Any:
        """Get the collection of the tasks only."""
        if collection != "tasks":
            raise KeyError(collection)
        return get_collection(db, collection)

    monkeypatch.setattr(task_runs_module, "get_collection", tasks_only)
    assert client.get(f"{BASE_PATH}/tasks").status_code == 200
    assert client.get(f"{BASE_PATH}/tasks/{task_id}").status_code == 200


def test_provenance_outcome_unknown_not_stored(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempts are stored also if storing the outcome failed."""

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Fail as if the response timed out."""
        raise BackendOutcomeUnknown("timed out")

    def update_task_state(self: DbDocumentConnector, **kwargs: Any) -> bool:
        """Fail to store the state."""
        raise RuntimeError("database down")

    monkeypatch.setattr(backend, "create_task", create_task)
    monkeypatch.setattr(DbDocumentConnector, "update_task_state", update_task_state)
    client.application.config["PROPAGATE_EXCEPTIONS"] = False
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 500
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["provenance"]["final_reason"] == "outcome_unknown"
    (attempt,) = document["provenance"]["attempts"]
    assert attempt["outcome"] == "unknown"


def test_provenance_tracking_failed(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accepted attempt is stored also if the tracking can't be started."""

    def apply_async(*args: Any, **kwargs: Any) -> None:
        """Fail as if the broker were down."""
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        task_runs_module.task__track_task_progress, "apply_async", apply_async
    )
    client.application.config["PROPAGATE_EXCEPTIONS"] = False
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 500
    (document,) = mongo_client[DB]["tasks"].find()
    (attempt,) = document["provenance"]["attempts"]
    assert attempt["outcome"] == "accepted"
