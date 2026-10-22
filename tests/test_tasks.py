"""Tests of the TES API endpoints, with a fake TES backend."""

# pylint: disable=redefined-outer-name

from typing import Any

from flask.testing import FlaskClient
import mongomock
import pytest
import tes

from pro_tes.ga4gh.tes import task_runs

BASE_PATH = "/ga4gh/tes/v1"
TASK = {
    "name": "hello",
    "executors": [
        {"image": "alpine", "command": ["echo", "hello"], "ignore_error": True}
    ],
}


class FakeBackend:
    """Fake TES backend: tasks are queued; records tasks and cancellations."""

    tasks: list[tes.Task] = []
    canceled: list[str] = []

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Create a client; arguments are ignored."""

    def get_service_info(self) -> tes.ServiceInfo:
        """Get the service info."""
        return tes.ServiceInfo(name="Fake")

    def create_task(self, task: tes.Task) -> str:
        """Create a task."""
        self.tasks.append(task)
        return f"remote-{len(self.tasks)}"

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get a task: queued."""
        return tes.Task(id=task_id, state="QUEUED", executors=[])

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task."""
        self.canceled.append(task_id)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> type[FakeBackend]:
    """Fake TES backend; tasks are not tracked by Celery."""
    monkeypatch.setattr(FakeBackend, "tasks", [])
    monkeypatch.setattr(FakeBackend, "canceled", [])
    monkeypatch.setattr(task_runs.tes, "HTTPClient", FakeBackend)
    monkeypatch.setattr(
        task_runs.task__track_task_progress,
        "apply_async",
        lambda *args, **kwargs: None,
    )
    return FakeBackend


def test_tasks(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: mongomock.MongoClient,
) -> None:
    """Tasks are relayed, listed, served and canceled."""
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    task_id = response.get_json()["id"]
    assert backend.tasks[0].executors[0].ignore_error is True

    response = client.get(f"{BASE_PATH}/tasks", query_string={"view": "MINIMAL"})
    assert response.status_code == 200
    # the state is set by the Celery task tracking the task (not run here)
    assert [task["id"] for task in response.get_json()["tasks"]] == [task_id]

    response = client.get(
        f"{BASE_PATH}/tasks/{task_id}", query_string={"view": "FULL"}
    )
    assert response.status_code == 200
    task = response.get_json()
    assert task["name"] == "hello"
    assert task["executors"][0]["ignore_error"] is True
    (forward,) = task["logs"][0]["metadata"]["forwarded_to"]
    assert forward["id"] == "remote-1"
    assert forward["forward_time"]

    # as set by the Celery task
    mongo_client["taskStore"]["tasks"].update_one(
        {"task.id": task_id}, {"$set": {"task.state": "QUEUED"}}
    )
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 200
    assert backend.canceled == ["remote-1"]
    response = client.get(
        f"{BASE_PATH}/tasks/{task_id}", query_string={"view": "MINIMAL"}
    )
    assert response.get_json()["state"] == "CANCELED"
