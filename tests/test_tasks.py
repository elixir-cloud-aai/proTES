"""Tests of the TES API endpoints, with a fake TES backend."""

# pylint: disable=redefined-outer-name

from typing import Any, ClassVar

from flask.testing import FlaskClient
from pymongo import MongoClient
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

    tasks: ClassVar[list[tes.Task]] = []
    canceled: ClassVar[list[str]] = []

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


@pytest.fixture
def task_id(client: FlaskClient, backend: type[FakeBackend]) -> str:
    """Identifier of a task relayed to the fake backend."""
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert len(backend.tasks) == 1
    return str(response.get_json()["id"])


def set_state(
    mongo_client: MongoClient[dict[str, Any]], task_id: str, state: str
) -> None:
    """Set the state of a stored task, as the Celery task does."""
    mongo_client["taskStore"]["tasks"].update_one(
        {"task.id": task_id}, {"$set": {"task.state": state}}
    )


def test_create_task(task_id: str, backend: type[FakeBackend]) -> None:
    """Tasks are relayed with `ignore_error` of their executors (#176)."""
    assert task_id
    assert backend.tasks[0].executors[0].ignore_error is True


def test_list_tasks(client: FlaskClient, task_id: str) -> None:
    """Tasks are listed."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"view": "MINIMAL"})
    assert response.status_code == 200
    assert [task["id"] for task in response.get_json()["tasks"]] == [task_id]


def test_get_task(client: FlaskClient, task_id: str) -> None:
    """Tasks are served with their executors and forwards."""
    response = client.get(f"{BASE_PATH}/tasks/{task_id}", query_string={"view": "FULL"})
    assert response.status_code == 200
    task = response.get_json()
    assert task["name"] == "hello"
    assert task["executors"][0]["ignore_error"] is True
    (forward,) = task["logs"][0]["metadata"]["forwarded_to"]
    assert forward["id"] == "remote-1"
    assert forward["forward_time"]


def test_cancel_task(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks are canceled at the last forward, i.e., proTES's own."""
    collection = mongo_client["taskStore"]["tasks"]
    document = collection.find_one({"task.id": task_id})
    assert document is not None
    forwards = document["task"]["logs"][0]["metadata"]["forwarded_to"]
    upstream = {"id": "upstream-1", "url": "https://upstream.example.org"}
    collection.update_one(
        {"task.id": task_id},
        {"$set": {"task.logs.0.metadata.forwarded_to": [upstream, *forwards]}},
    )
    set_state(mongo_client, task_id, "QUEUED")
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 200
    assert backend.canceled == ["remote-1"]
    response = client.get(
        f"{BASE_PATH}/tasks/{task_id}", query_string={"view": "MINIMAL"}
    )
    assert response.get_json()["state"] == "CANCELED"


def test_cancel_task_nested_forwards(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks stored with nested forwards are served as lists, and canceled.

    Earlier versions stored the forwards nested.
    """
    mongo_client["taskStore"]["tasks"].update_one(
        {"task.id": task_id},
        {
            "$set": {
                "task.logs.0.metadata.forwarded_to": {
                    "id": "remote-1",
                    "url": "https://tes.example.org",
                }
            }
        },
    )
    for path, query in (
        (f"/tasks/{task_id}", {"view": "FULL"}),
        ("/tasks", {"view": "FULL"}),
    ):
        response = client.get(f"{BASE_PATH}{path}", query_string=query)
        tasks = response.get_json().get("tasks") or [response.get_json()]
        assert tasks[0]["logs"][0]["metadata"]["forwarded_to"] == [
            {"id": "remote-1", "url": "https://tes.example.org"}
        ]
    set_state(mongo_client, task_id, "RUNNING")
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 200
    assert backend.canceled == ["remote-1"]
