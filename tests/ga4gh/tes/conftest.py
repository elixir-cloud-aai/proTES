"""Fixtures and helpers of the tests of the TES API, with fake TES backends."""

from typing import Any, ClassVar

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_custom_config
from pymongo import MongoClient
import pytest
import requests
import tes

from pro_tes.config_models import CustomConfig, TesBackend
from pro_tes.ga4gh.tes import backends, task_runs
from tests.conftest import DB

BASE_PATH = "/ga4gh/tes/v1"
TASK: dict[str, Any] = {
    "name": "hello",
    "executors": [
        {"image": "alpine", "command": ["echo", "hello"], "ignore_error": True}
    ],
}
# Task with inputs and outputs, also an input with content instead of a URL
TASK_FILES: dict[str, Any] = {
    **TASK,
    "inputs": [
        {"url": "s3://bucket/in", "path": "/in", "type": "FILE"},
        {"content": "hello", "path": "/greeting", "type": "FILE"},
    ],
    "outputs": [{"url": "s3://bucket/out", "path": "/out", "type": "FILE"}],
}
# TES backends of the tests (`custom.tes.service_list`)
BACKENDS = ["https://tes1.example.org", "https://tes2.example.org"]


class FakeBackend:
    """Fake TES backends: tasks are queued.

    Records tasks, cancellations and the backends that clients were created
    for; backends can be made invalid, failing, or not serving their tasks.

    Attributes:
        url: URL of the backend.
        backend: Configuration of the backend.
    """

    # Tasks by backend URL
    tasks: ClassVar[dict[str, list[tes.Task]]] = {}
    canceled: ClassVar[list[str]] = []
    # Configurations of the backends that clients were created for
    clients: ClassVar[list[TesBackend]] = []
    # Backends whose URL is invalid, that reject tasks, and that don't serve
    # their tasks
    invalid: ClassVar[set[str]] = set()
    failing: ClassVar[set[str]] = set()
    no_tasks: ClassVar[set[str]] = set()

    def __init__(self, url: str, backend: TesBackend, **kwargs: Any) -> None:
        """Create a client of a backend; other arguments are ignored.

        Raises:
            ValueError: The URL is invalid.
        """
        if url.rstrip("/") in self.invalid:
            raise ValueError(f"Invalid URL: {url}")
        self.url = url.rstrip("/")
        self.backend = backend
        self.clients.append(backend)

    def create_task(self, task: tes.Task) -> str:
        """Create a task.

        Raises:
            requests.HTTPError: The backend rejects tasks.
        """
        if self.url in self.failing:
            raise requests.HTTPError("500")
        tasks = self.tasks.setdefault(self.url, [])
        tasks.append(task)
        return f"remote-{len(tasks)}"

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get a task: queued.

        Raises:
            requests.HTTPError: The backend doesn't serve its tasks.
        """
        if self.url in self.no_tasks:
            raise requests.HTTPError("404")
        return tes.Task(id=task_id, state="QUEUED", executors=[])

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task."""
        self.canceled.append(task_id)


@pytest.fixture
def tracked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Arguments of the Celery tasks started to track tasks (not run)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        task_runs.task__track_task_progress,
        "apply_async",
        lambda args, kwargs: calls.append(kwargs),
    )
    return calls


@pytest.fixture
def backend(
    monkeypatch: pytest.MonkeyPatch, app: FlaskApp, tracked: list[dict[str, Any]]
) -> type[FakeBackend]:
    """Fake TES backends `BACKENDS`; tasks are not tracked by Celery.

    The backends need no authentication (`auth: none`).
    """
    for name in ("invalid", "failing", "no_tasks"):
        monkeypatch.setattr(FakeBackend, name, set())
    monkeypatch.setattr(FakeBackend, "tasks", {})
    monkeypatch.setattr(FakeBackend, "canceled", [])
    monkeypatch.setattr(FakeBackend, "clients", [])
    monkeypatch.setattr(backends, "TesClient", FakeBackend)
    with app.app.app_context():
        get_custom_config(CustomConfig).tes.service_list = [
            TesBackend(url=url, auth="none") for url in BACKENDS
        ]
    return FakeBackend


def relayed(backend: type[FakeBackend]) -> list[tuple[str, tes.Task]]:
    """Get the tasks relayed to the fake backends, with their URLs."""
    return [(url, task) for url, tasks in backend.tasks.items() for task in tasks]


@pytest.fixture
def task_id(client: FlaskClient, backend: type[FakeBackend]) -> str:
    """Identifier of a task relayed to a fake backend."""
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert len(relayed(backend)) == 1
    return str(response.get_json()["id"])


def stored(mongo_client: MongoClient[dict[str, Any]], task_id: str) -> dict[str, Any]:
    """Get the stored document of a task."""
    document = mongo_client[DB]["tasks"].find_one({"task.id": task_id})
    assert document is not None
    return document


def set_state(
    mongo_client: MongoClient[dict[str, Any]], task_id: str, state: str
) -> None:
    """Set the state of a stored task, as the Celery task does."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$set": {"task.state": state}}
    )
