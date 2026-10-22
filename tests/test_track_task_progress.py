"""Tests of the Celery task tracking tasks at TES backends."""

from typing import Any

import mongomock
import pytest
import tes

from pro_tes.app import init_app
from pro_tes.tasks import track_task_progress


class FakeClient:  # pylint: disable=too-few-public-methods
    """TES client whose task is complete."""

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Create the client; arguments are ignored."""

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get the task: complete, with one log."""
        return tes.Task(
            id=task_id,
            state="COMPLETE",
            logs=[tes.TaskLog(logs=[], outputs=[])],
        )


@pytest.mark.parametrize("db_name", [None, "protes"])
def test_task_tracked(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: mongomock.MongoClient,
    db_name: str | None,
) -> None:
    """The task state is stored in the configured database (cf. B6).

    That is, database `taskStore`, or the database named by environment
    variable `MONGO_DBNAME`.
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
