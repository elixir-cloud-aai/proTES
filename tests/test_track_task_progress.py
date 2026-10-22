"""Tests of the Celery task tracking tasks at TES backends."""

from typing import Any

from connexion import FlaskApp
from foca import get_collection
from pymongo import MongoClient
import pytest
import tes

from pro_tes.app import init_app
from pro_tes.tasks import track_task_progress
from pro_tes.utils.db import DbDocumentConnector


class FakeClient:
    """TES client whose task is complete."""

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Create the client; arguments are ignored."""

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get the task: complete, with one log."""
        output = tes.OutputFileLog(url="s3://bucket/out", path="/out", size_bytes=10)
        return tes.Task(
            id=task_id,
            state="COMPLETE",
            logs=[tes.TaskLog(logs=[], outputs=[output])],
        )


@pytest.mark.parametrize("db_name", [None, "protes"])
def test_task_tracked(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
    db_name: str | None,
) -> None:
    """The state and outputs are stored in the configured database.

    That is, database `taskStore`, or the database named by environment
    variable `MONGO_DBNAME` (instead of a hard-coded `taskStore`); py-tes
    returns output sizes as integers.
    """
    if db_name is not None:
        monkeypatch.setenv("MONGO_DBNAME", db_name)
    app = init_app()
    collection = mongo_client[db_name or "taskStore"]["tasks"]
    collection.insert_one(
        {
            "worker_id": "worker-1",
            "task": {
                "id": "TASK01",
                "state": "QUEUED",
                "executors": [],
                "logs": [{"logs": [], "outputs": []}],
            },
        }
    )
    monkeypatch.setattr(track_task_progress.tes, "HTTPClient", FakeClient)
    monkeypatch.setattr(track_task_progress, "sleep", lambda seconds: None)
    with app.app.app_context():
        track_task_progress.task__track_task_progress.run(
            worker_id="worker-1",
            remote_host="https://tes.example.org",
            remote_base_path="",
            remote_task_id="remote-1",
            user=None,
            password=None,
        )
    document = collection.find_one({"worker_id": "worker-1"})
    assert document is not None
    assert document["task"]["state"] == "COMPLETE"
    assert document["task"]["logs"][0]["outputs"] == [
        {"url": "s3://bucket/out", "path": "/out", "size_bytes": "10"}
    ]


def test_document_missing(app: FlaskApp) -> None:
    """Updates of missing documents fail."""
    with app.app.app_context():
        connector = DbDocumentConnector(
            collection=get_collection("taskStore", "tasks"), worker_id="none"
        )
        with pytest.raises(ValueError, match="No database document"):
            connector.upsert_fields_in_root_object(root="task", state="QUEUED")
