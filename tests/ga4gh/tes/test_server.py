"""Tests of the TES API endpoints, with fake TES backends."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar
from unittest.mock import ANY

from connexion import FlaskApp
from flask.testing import FlaskClient
from pydantic import ValidationError
from pymongo import MongoClient
import pytest
import requests
import tes

from pro_tes.config_models import ListTasks
from pro_tes.ga4gh.tes import task_runs
from pro_tes.ga4gh.tes.models import TesTask
from pro_tes.plugins.middlewares.task_distribution.random import (
    TaskDistributionRandom,
)
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
    assert document["tes_endpoint"] == {
        "host": url,
        "base_path": "",
        "task_id": "remote-1",
    }
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


# Listing (#165), name prefixes, relay to backends, timestamps (#208)


def create_tasks(client: FlaskClient, names: list[str]) -> list[str]:
    """Create tasks with names, in order.

    Returns:
        The task identifiers, newest first (as listed).
    """
    ids = []
    for name in names:
        response = client.post(f"{BASE_PATH}/tasks", json={**TASK, "name": name})
        assert response.status_code == 200
        ids.append(response.get_json()["id"])
    return list(reversed(ids))


def list_tasks(client: FlaskClient, **query: Any) -> dict[str, Any]:
    """List tasks (view `MINIMAL`), checking the status."""
    response = client.get(
        f"{BASE_PATH}/tasks", query_string={"view": "MINIMAL", **query}
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.get_json()
    return body


def test_list_tasks_empty(client: FlaskClient) -> None:
    """Without tasks, an empty page is listed, without next page (#165)."""
    assert list_tasks(client) == {"tasks": [], "next_page_token": ""}


@pytest.mark.parametrize("page_size", [1, 2, 5, 6])
def test_list_tasks_pages(
    client: FlaskClient, backend: type[FakeBackend], page_size: int
) -> None:
    """Paging lists all tasks once, newest first, and ends (#165).

    Only the last page has an empty `next_page_token`.
    """
    ids = create_tasks(client, [f"task{index}" for index in range(5)])
    listed: list[str] = []
    tokens: list[str] = []
    token = None
    while True:
        query = {"page_size": page_size}
        if token is not None:
            query["page_token"] = token
        page = list_tasks(client, **query)
        assert 0 < len(page["tasks"]) <= page_size
        listed.extend(task["id"] for task in page["tasks"])
        token = page["next_page_token"]
        tokens.append(token)
        if not token:
            break
        assert len(tokens) <= 5
    assert listed == ids
    assert len(tokens) == -(-5 // page_size)


def test_list_tasks_empty_token(client: FlaskClient, task_id: str) -> None:
    """An empty page token (of a last page) lists the first page."""
    assert [task["id"] for task in list_tasks(client, page_token="")["tasks"]] == [
        task_id
    ]


@pytest.mark.parametrize("token", ["invalid", "0" * 23, "g" * 24])
def test_list_tasks_invalid_token(client: FlaskClient, token: str) -> None:
    """Invalid page tokens are rejected (`400`)."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"page_token": token})
    assert response.status_code == 400
    assert response.get_json()["status"] == 400


@pytest.mark.parametrize("page_size", [0, -1])
def test_list_tasks_invalid_page_size(client: FlaskClient, page_size: int) -> None:
    """Page sizes must be positive (`400`)."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"page_size": page_size})
    assert response.status_code == 400


def test_list_tasks_page_sizes(
    app: FlaskApp, client: FlaskClient, backend: type[FakeBackend]
) -> None:
    """Pages have the default size, and at most the maximum size."""
    with app.app.app_context():
        config = get_custom_config().controllers.list_tasks
        config.default_page_size = 2
        config.max_page_size = 3
    create_tasks(client, ["a", "b", "c", "d"])
    assert len(list_tasks(client)["tasks"]) == 2
    assert len(list_tasks(client, page_size=100)["tasks"]) == 3


def test_page_size_config() -> None:
    """The default page size must not exceed the maximum."""
    assert ListTasks().default_page_size == 256
    assert ListTasks().max_page_size == 2047
    with pytest.raises(ValidationError, match="exceeds max_page_size"):
        ListTasks(default_page_size=10, max_page_size=5)
    assert ListTasks(default_page_size=5, max_page_size=5).max_page_size == 5
    with pytest.raises(ValidationError):
        ListTasks(default_page_size=0)
    with pytest.raises(ValidationError):
        ListTasks(max_page_size=0)
    with pytest.raises(ValidationError):
        ListTasks(max_page_size=2048)


def test_default_view(client: FlaskClient, task_id: str) -> None:
    """Without a `view`, tasks are returned in the `MINIMAL` view."""
    minimal = {"id": task_id, "state": ANY}
    assert client.get(f"{BASE_PATH}/tasks/{task_id}").get_json() == minimal
    assert client.get(f"{BASE_PATH}/tasks").get_json()["tasks"] == [minimal]


@pytest.mark.parametrize(
    "prefix, names",
    [
        ("a.b", ["a.bc", "a.b"]),
        (".*", [".*x"]),
        ("[", ["[x"]),
        ("(", []),
        ("b", ["b"]),
        ("", ["b", "[x", ".*x", "axb", "a.bc", "a.b"]),
    ],
)
def test_list_tasks_name_prefix(
    client: FlaskClient, backend: type[FakeBackend], prefix: str, names: list[str]
) -> None:
    """Names match the prefix literally, not as a regular expression."""
    ids = create_tasks(client, ["a.b", "a.bc", "axb", ".*x", "[x", "b"])
    by_id = dict(zip(ids, ["b", "[x", ".*x", "axb", "a.bc", "a.b"], strict=True))
    tasks = list_tasks(client, name_prefix=prefix, page_size=10)["tasks"]
    assert [by_id[task["id"]] for task in tasks] == names


def test_list_tasks_name_prefix_null(client: FlaskClient) -> None:
    """Name prefixes with null characters are rejected."""
    response = client.get(f"{BASE_PATH}/tasks", query_string={"name_prefix": "a\x00"})
    assert response.status_code == 400


@pytest.mark.parametrize("view", ["BASIC", "FULL"])
def test_views_logs(
    client: FlaskClient,
    task_id: str,
    mongo_client: MongoClient[dict[str, Any]],
    view: str,
) -> None:
    """`BASIC` has no system logs and executor outputs, `FULL` has them."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id},
        {
            "$set": {
                "task.logs.0.system_logs": ["started"],
                "task.logs.0.logs": [
                    {"exit_code": 0, "stdout": "out", "stderr": "err"}
                ],
            }
        },
    )
    for task in (
        client.get(
            f"{BASE_PATH}/tasks/{task_id}", query_string={"view": view}
        ).get_json(),
        list_tasks(client, view=view)["tasks"][0],
    ):
        (log,) = task["logs"]
        (executor_log,) = log["logs"]
        assert executor_log["exit_code"] == 0
        if view == "BASIC":
            assert "system_logs" not in log
            assert "stdout" not in executor_log
            assert "stderr" not in executor_log
        else:
            assert log["system_logs"] == ["started"]
            assert (executor_log["stdout"], executor_log["stderr"]) == ("out", "err")


@pytest.fixture
def in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks are relayed to the backends in the configured order."""

    def ordered(
        self: TaskDistributionRandom, tes_urls: list[str], request: Any
    ) -> None:
        """Keep the order."""
        self.tes_urls = list(tes_urls)

    monkeypatch.setattr(TaskDistributionRandom, "_set_tes_urls", ordered)


# Inputs with credentials in URLs
TASK_CREDENTIALS: dict[str, Any] = {
    **TASK_FILES,
    "inputs": [
        {"url": "ftp://user:secret@ftp.example.org/in", "path": "/in", "type": "FILE"},
        {"content": "hello", "path": "/greeting", "type": "FILE"},
    ],
}


def test_create_task_inputs_kept(
    client: FlaskClient, backend: type[FakeBackend], in_order: None
) -> None:
    """Other backends get inputs and outputs as objects, without credentials.

    Inputs with content are kept.
    """
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_CREDENTIALS)
    assert response.status_code == 200
    ((_, task),) = relayed(backend)
    assert [(item.url, item.path, item.content) for item in task.inputs] == [
        ("ftp://ftp.example.org/in", "/in", None),
        (None, "/greeting", "hello"),
    ]
    assert [(item.url, item.path) for item in task.outputs] == [
        ("s3://bucket/out", "/out")
    ]


def test_create_task_payload_per_backend(
    client: FlaskClient, backend: type[FakeBackend], in_order: None
) -> None:
    """Each backend gets the task as received, without changes for others.

    The first backend (credentials removed) rejects the task, the next one
    (Funnel) gets the credentials.
    """
    backend.failing.add(BACKENDS[0])
    backend.funnel.add(BACKENDS[1])
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_CREDENTIALS)
    assert response.status_code == 200
    ((url, task),) = relayed(backend)
    assert url == BACKENDS[1]
    assert task.inputs[0].url == "ftp://user:secret@ftp.example.org/in"


def test_timestamps(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Times are set by proTES, in UTC, in RFC 3339 format.

    Also the creation time, which clients can't set (`400`).
    """
    response = client.post(
        f"{BASE_PATH}/tasks",
        json={**TASK, "creation_time": "2000-01-01T00:00:00Z"},
    )
    assert response.status_code == 400
    before = datetime.now(timezone.utc)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    after = datetime.now(timezone.utc)
    task = stored(mongo_client, response.get_json()["id"])["task"]
    (log,) = task["logs"]
    times = [task["creation_time"], log["start_time"], log["end_time"]]
    times.append(log["metadata"]["forwarded_to"][0]["forward_time"])
    for value in times:
        parsed = datetime.fromisoformat(value)
        assert parsed.utcoffset() == timedelta(0)
        assert before <= parsed <= after
    ((_, relayed_task),) = relayed(backend)
    # the TES instance sets its own creation time
    assert relayed_task.creation_time is None


def test_cancel_task_without_trace(
    app: FlaskApp,
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks are canceled without forwards in their logs (`execution_trace`)."""
    with app.app.app_context():
        get_custom_config().storeLogs.execution_trace = False
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    task_id = response.get_json()["id"]
    assert stored(mongo_client, task_id)["task"]["logs"][0]["metadata"] == {}
    set_state(mongo_client, task_id, "RUNNING")
    assert client.post(f"{BASE_PATH}/tasks/{task_id}:cancel").status_code == 200
    assert backend.canceled == ["remote-1"]


def test_cancel_task_legacy(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks stored by earlier versions are canceled at their last forward."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$unset": {"tes_endpoint.task_id": ""}}
    )
    set_state(mongo_client, task_id, "RUNNING")
    assert client.post(f"{BASE_PATH}/tasks/{task_id}:cancel").status_code == 200
    assert backend.canceled == ["remote-1"]
    # without an execution trace
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id},
        {
            "$set": {
                "task.logs.0.metadata": {"remote_task_id": "remote-2"},
                "task.state": "RUNNING",
            }
        },
    )
    assert client.post(f"{BASE_PATH}/tasks/{task_id}:cancel").status_code == 200
    assert backend.canceled == ["remote-1", "remote-2"]


def test_cancel_task_no_remote_id(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Cancelable tasks without a remote identifier can't be canceled (`500`)."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id},
        {
            "$unset": {"tes_endpoint.task_id": ""},
            "$set": {"task.logs.0.metadata": {}, "task.state": "RUNNING"},
        },
    )
    assert client.post(f"{BASE_PATH}/tasks/{task_id}:cancel").status_code == 500
    assert backend.canceled == []
    assert stored(mongo_client, task_id)["task"]["state"] == "RUNNING"
