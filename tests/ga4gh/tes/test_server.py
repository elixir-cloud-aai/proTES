"""Tests of the TES API endpoints, with fake TES backends."""

from copy import deepcopy
from typing import Any, ClassVar

from connexion import FlaskApp
from flask.testing import FlaskClient
from pymongo import MongoClient
import pytest
import requests
import tes

from pro_tes.ga4gh.tes import task_runs
from pro_tes.ga4gh.tes.models import TesTask
from pro_tes.tasks import track_task_progress
from pro_tes.utils.misc import get_custom_config
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

    Records tasks and cancellations; backends can be made unreachable,
    invalid, or Funnel.

    Attributes:
        url: URL of the backend.
    """

    # Tasks by backend URL
    tasks: ClassVar[dict[str, list[tes.Task]]] = {}
    canceled: ClassVar[list[str]] = []
    # Backends whose URL is invalid, that reject tasks, or whose service info
    # is unavailable
    invalid: ClassVar[set[str]] = set()
    failing: ClassVar[set[str]] = set()
    no_service_info: ClassVar[set[str]] = set()
    # Backends that are Funnel, and that don't serve their tasks
    funnel: ClassVar[set[str]] = set()
    no_tasks: ClassVar[set[str]] = set()

    def __init__(self, url: str, **kwargs: Any) -> None:
        """Create a client; arguments other than the URL are ignored.

        Raises:
            ValueError: The URL is invalid.
        """
        if url.rstrip("/") in self.invalid:
            raise ValueError(f"Invalid URL: {url}")
        self.url = url.rstrip("/")

    def get_service_info(self) -> tes.ServiceInfo:
        """Get the service info.

        Raises:
            requests.HTTPError: The service info is unavailable.
        """
        if self.url in self.no_service_info:
            raise requests.HTTPError("503")
        return tes.ServiceInfo(name="Funnel" if self.url in self.funnel else "Fake")

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
    """Fake TES backends `BACKENDS`; tasks are not tracked by Celery."""
    for name in ("invalid", "failing", "no_service_info", "funnel", "no_tasks"):
        monkeypatch.setattr(FakeBackend, name, set())
    monkeypatch.setattr(FakeBackend, "tasks", {})
    monkeypatch.setattr(FakeBackend, "canceled", [])
    monkeypatch.setattr(task_runs.tes, "HTTPClient", FakeBackend)
    with app.app.app_context():
        get_custom_config().tes.service_list = list(BACKENDS)
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


def test_create_task(
    task_id: str,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks are relayed with `ignore_error` of their executors (#176).

    The backend is stored, and a Celery task tracks the task.
    """
    ((url, task),) = relayed(backend)
    assert task.executors[0].ignore_error is True
    document = stored(mongo_client, task_id)
    assert document["tes_endpoint"] == {"host": url, "base_path": ""}
    assert tracked == [
        {
            "worker_id": document["worker_id"],
            "remote_host": url,
            "remote_base_path": "",
            "remote_task_id": "remote-1",
            "user": None,
            "password": None,
        }
    ]


@pytest.mark.parametrize("task", [{}, {"executors": [{"command": ["true"]}]}])
def test_create_task_invalid(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    task: dict[str, Any],
) -> None:
    """Invalid tasks are rejected (`400`), and neither stored nor relayed."""
    response = client.post(f"{BASE_PATH}/tasks", json=task)
    assert response.status_code == 400
    assert response.get_json()["status"] == 400
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


@pytest.mark.parametrize("unusable", ["invalid", "failing"])
def test_create_task_next_backend(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    unusable: str,
) -> None:
    """Tasks go to the next backend if a backend is invalid or rejects them."""
    getattr(backend, unusable).add(BACKENDS[0])
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert [url for url, _ in relayed(backend)] == [BACKENDS[1]]
    document = stored(mongo_client, response.get_json()["id"])
    assert document["tes_endpoint"]["host"] == BACKENDS[1]
    (forward,) = document["task"]["logs"][0]["metadata"]["forwarded_to"]
    assert forward["url"] == BACKENDS[1]


def test_create_task_no_backend(
    client: FlaskClient,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks that no backend accepts fail (`500`); they are stored as such."""
    backend.failing.update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    assert response.get_json()["status"] == 500
    assert not tracked
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["task"]["state"] == "SYSTEM_ERROR"


def test_create_task_state_unavailable(
    client: FlaskClient,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks are tracked even if the backend doesn't serve them right away."""
    backend.no_tasks.update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert stored(mongo_client, response.get_json()["id"])
    assert len(tracked) == 1


def test_create_task_funnel(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Funnel backends get the inputs and outputs as they are."""
    backend.funnel.update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES)
    assert response.status_code == 200
    ((_, task),) = relayed(backend)
    assert [(item.url, item.path, item.content) for item in task.inputs] == [
        ("s3://bucket/in", "/in", None),
        (None, "/greeting", "hello"),
    ]
    assert [(item.url, item.path) for item in task.outputs] == [
        ("s3://bucket/out", "/out")
    ]


@pytest.mark.parametrize("info", ["no_service_info", None])
def test_create_task_other_backend(
    client: FlaskClient, backend: type[FakeBackend], info: str | None
) -> None:
    """Tasks with files are relayed to other backends, or without service info."""
    if info is not None:
        getattr(backend, info).update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES)
    assert response.status_code == 200
    assert len(relayed(backend)) == 1


def test_task_original_unchanged(
    app: FlaskApp,
    client: FlaskClient,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task as received is stored once, and never changed (#142).

    Neither by the middlewares (which add the TES backends), nor by the
    tracking of the task, nor by its cancellation.
    """
    request = deepcopy(TASK_FILES)
    response = client.post(f"{BASE_PATH}/tasks", json=request)
    assert response.status_code == 200
    assert request == TASK_FILES
    task_id = response.get_json()["id"]
    expected = TesTask(**TASK_FILES).model_dump(mode="json", exclude_none=True)
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["task_original"] == expected
    assert "tes_urls" not in document["task"]

    # tracked until complete, then canceled (no-op)
    def complete(self: FakeBackend, task_id: str, **kwargs: Any) -> tes.Task:
        """Get a complete task."""
        return tes.Task(id=task_id, state="COMPLETE", logs=[tes.TaskLog(logs=[])])

    monkeypatch.setattr(FakeBackend, "get_task", complete)
    monkeypatch.setattr(track_task_progress.tes, "HTTPClient", FakeBackend)
    monkeypatch.setattr(track_task_progress, "sleep", lambda seconds: None)
    with app.app.app_context():
        track_task_progress.task__track_task_progress.run(**tracked[0])
    assert client.post(f"{BASE_PATH}/tasks/{task_id}:cancel").status_code == 200
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["task"]["state"] == "COMPLETE"
    assert document["task_original"] == expected


def test_list_tasks(client: FlaskClient, task_id: str) -> None:
    """Tasks are listed."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"view": "MINIMAL"})
    assert response.status_code == 200
    (task,) = response.get_json()["tasks"]
    assert set(task) == {"id", "state"}
    assert task["id"] == task_id


@pytest.mark.parametrize("view", ["BASIC", "FULL"])
def test_list_tasks_views(client: FlaskClient, task_id: str, view: str) -> None:
    """Tasks are listed with their executors in views `BASIC` and `FULL`."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"view": view})
    assert response.status_code == 200
    (task,) = response.get_json()["tasks"]
    assert task["id"] == task_id
    assert task["executors"][0]["image"] == "alpine"


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


def test_get_task_views(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """`MINIMAL` (the default) has the ID and state, `BASIC` no input contents."""
    task_id = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES).get_json()["id"]
    url = f"{BASE_PATH}/tasks/{task_id}"
    minimal = client.get(url, query_string={"view": "MINIMAL"}).get_json()
    assert set(minimal) == {"id", "state"}
    assert minimal["id"] == task_id
    assert client.get(url).get_json() == minimal
    basic = client.get(url, query_string={"view": "BASIC"}).get_json()
    assert [item.get("content") for item in basic["inputs"]] == [None, None]
    full = client.get(url, query_string={"view": "FULL"}).get_json()
    assert [item.get("content") for item in full["inputs"]] == [None, "hello"]


def test_get_task_not_found(client: FlaskClient) -> None:
    """Unknown tasks are not found (`404`)."""
    response = client.get(f"{BASE_PATH}/tasks/UNKNOWN")
    assert response.status_code == 404


def test_cancel_task(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks are canceled at the last forward, i.e., proTES's own."""
    collection = mongo_client[DB]["tasks"]
    forwards = stored(mongo_client, task_id)["task"]["logs"][0]["metadata"][
        "forwarded_to"
    ]
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


@pytest.mark.parametrize("state", ["COMPLETE", "EXECUTOR_ERROR", "CANCELED"])
def test_cancel_task_finished(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    state: str,
) -> None:
    """Finished tasks are left as they are."""
    set_state(mongo_client, task_id, state)
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 200
    assert not backend.canceled
    assert stored(mongo_client, task_id)["task"]["state"] == state


def test_cancel_task_not_found(client: FlaskClient) -> None:
    """Unknown tasks are not found (`404`)."""
    response = client.post(f"{BASE_PATH}/tasks/UNKNOWN:cancel")
    assert response.status_code == 404


def test_cancel_task_nested_forwards(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks stored with nested forwards are served as lists, and canceled.

    Earlier versions stored the forwards nested.
    """
    mongo_client[DB]["tasks"].update_one(
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
