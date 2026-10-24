"""Tests of the TES API endpoints, with fake TES backends."""

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import ANY

from connexion import FlaskApp
from flask.testing import FlaskClient
from pydantic import ValidationError
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
import pytest
import requests
import tes

from pro_tes.config_models import ListTasks
from pro_tes.exceptions import BackendAuthError, BackendOutcomeUnknown, BadRequest
from pro_tes.ga4gh.tes import ownership, task_runs
from pro_tes.ga4gh.tes.models import DbDocument, TesTask
from pro_tes.middleware.middleware_handler import MiddlewareHandler
from pro_tes.plugins.middlewares.task_distribution.random import (
    TaskDistributionRandom,
)
from pro_tes.tasks import track_task_progress
from pro_tes.utils.misc import get_custom_config
from tests.conftest import DB, OTHER_USER, USER
from tests.ga4gh.tes.conftest import (
    BACKENDS,
    BASE_PATH,
    TASK,
    TASK_FILES,
    FakeBackend,
    relayed,
    set_state,
    stored,
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
        }
    ]
    assert "basic_auth" not in document


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
    in_order: None,
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
    """Tasks that no backend accepts fail (`500`); they are not kept.

    Neither the task nor the owner's roles on it.
    """
    backend.failing.update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    assert response.get_json()["status"] == 500
    assert "not kept" in response.get_json()["detail"]
    assert not tracked
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


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


def test_create_task_state_invalid(
    client: FlaskClient,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks are stored with their backend if it serves them invalid.

    E.g., with an unknown state: the backend accepted the task, so it is
    tracked (and not created again by clients retrying after errors).
    """
    monkeypatch.setattr(
        backend,
        "get_task",
        lambda self, task_id, **kwargs: tes.Task(id=task_id, state="DONE"),
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    document = stored(mongo_client, response.get_json()["id"])
    assert document["tes_endpoint"]["task_id"] == "remote-1"
    assert len(tracked) == 1


def test_create_task_files(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Backends get the inputs and outputs as they are."""
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


def test_create_task_backend_config(
    client: FlaskClient, backend: type[FakeBackend], in_order: None
) -> None:
    """Clients get the configuration of their backend (for its credentials)."""
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    assert [config.url for config in backend.clients] == [BACKENDS[0]]


def test_create_task_unknown_backend(
    client: FlaskClient,
    backend: type[FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backends that are not configured (e.g., of middlewares) are skipped.

    proTES has no credentials for them.
    """

    def unknown_first(
        self: TaskDistributionRandom, tes_urls: list[str], request: Any
    ) -> None:
        """Try an unknown backend first."""
        self.tes_urls = ["https://unknown.example.org", *tes_urls]

    monkeypatch.setattr(TaskDistributionRandom, "_set_tes_urls", unknown_first)
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    assert "https://unknown.example.org" not in backend.tasks
    assert len(relayed(backend)) == 1


def test_create_task_backend_auth_error(
    client: FlaskClient,
    backend: type[FakeBackend],
    in_order: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks go to the next backend if there are no credentials for a backend."""

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Fail for the first backend, as if no token was obtained."""
        if self.url == BACKENDS[0]:
            raise BackendAuthError("no token")
        return "remote-1"

    monkeypatch.setattr(backend, "create_task", create_task)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert [config.url for config in backend.clients] == BACKENDS


def test_create_task_outcome_unknown(
    client: FlaskClient,
    backend: type[FakeBackend],
    tracked: list[dict[str, Any]],
    mongo_client: MongoClient[dict[str, Any]],
    in_order: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks that a backend may have accepted are not sent elsewhere (`502`).

    They are stored as `SYSTEM_ERROR`, with the backend, without remote
    identifier, and are not tracked.
    """

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Fail as if the response timed out."""
        raise BackendOutcomeUnknown("timed out")

    monkeypatch.setattr(backend, "create_task", create_task)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 502
    assert "may have processed it" in response.get_json()["detail"]
    assert [config.url for config in backend.clients] == [BACKENDS[0]]
    assert not tracked
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["task"]["state"] == "SYSTEM_ERROR"
    assert document["tes_endpoint"] == {"host": BACKENDS[0], "base_path": ""}


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

    monkeypatch.setattr(backend, "get_task", complete)
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


def test_list_tasks_page_sizes_full(
    app: FlaskApp, client: FlaskClient, backend: type[FakeBackend]
) -> None:
    """Pages of the view `FULL` have at most their own maximum size.

    Also by default, and at most the maximum size of all views.
    """
    with app.app.app_context():
        config = get_custom_config().controllers.list_tasks
        config.default_page_size = 3
        config.max_page_size = 4
        config.max_page_size_full = 2
    ids = create_tasks(client, ["a", "b", "c", "d", "e"])
    page = list_tasks(client, view="FULL")
    assert [task["id"] for task in page["tasks"]] == ids[:2]
    assert len(list_tasks(client, view="FULL", page_size=100)["tasks"]) == 2
    assert len(list_tasks(client, view="BASIC", page_size=100)["tasks"]) == 4
    assert len(list_tasks(client, view="MINIMAL")["tasks"]) == 3
    next_page = list_tasks(client, view="FULL", page_token=page["next_page_token"])
    assert [task["id"] for task in next_page["tasks"]] == ids[2:4]
    with app.app.app_context():
        config.max_page_size_full = 10
    assert len(list_tasks(client, view="FULL", page_size=100)["tasks"]) == 4


def test_list_tasks_last_page_full(client: FlaskClient, task_id: str) -> None:
    """Pages that hold the remaining tasks have no next page token."""
    page = list_tasks(client, view="FULL")
    assert [task["id"] for task in page["tasks"]] == [task_id]
    assert page["next_page_token"] == ""


def test_page_size_config() -> None:
    """The default page size must not exceed the maximum."""
    assert ListTasks().default_page_size == 256
    assert ListTasks().max_page_size == 2047
    assert ListTasks().max_page_size_full == 64
    with pytest.raises(ValidationError):
        ListTasks(max_page_size_full=0)
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


@pytest.mark.parametrize(
    "contents, accepted",
    [
        (["a" * 8, "b" * 8], True),
        (["a" * 8, "b" * 9], False),
        # bytes, not characters
        (["\u00e4" * 8], True),
        (["\u00e4" * 8, "x"], False),
    ],
)
def test_create_task_inline_content(
    app: FlaskApp,
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    contents: list[str],
    accepted: bool,
) -> None:
    """Tasks with larger inline contents than the limit are rejected (`413`).

    The contents of all inputs count together, in bytes (UTF-8); rejected
    tasks are neither stored nor relayed.

    Args:
        app: The app.
        client: Client of the app.
        backend: Fake backends.
        mongo_client: Client of the database.
        contents: Inline contents of the inputs.
        accepted: Whether the task is accepted.
    """
    with app.app.app_context():
        get_custom_config().controllers.post_task.max_inline_content = 16
    task = {
        **TASK,
        "inputs": [
            {"content": content, "path": f"/in{index}", "type": "FILE"}
            for index, content in enumerate(contents)
        ]
        + [{"url": "s3://bucket/in", "path": "/in", "type": "FILE"}],
    }
    response = client.post(f"{BASE_PATH}/tasks", json=task)
    if accepted:
        assert response.status_code == 200
        return
    assert response.status_code == 413
    assert "inline contents" in response.get_json()["detail"]
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


def test_create_task_inline_content_middleware(
    app: FlaskApp,
    client: FlaskClient,
    backend: type[FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inline contents that middlewares add count, too."""
    apply = TaskDistributionRandom.apply_middleware

    def add_content(self: TaskDistributionRandom, request: Any) -> Any:
        """Add a large inline content."""
        request = apply(self, request)
        request.json["inputs"] = [
            {"content": "x" * 32, "path": "/added", "type": "FILE"}
        ]
        return request

    monkeypatch.setattr(TaskDistributionRandom, "apply_middleware", add_content)
    with app.app.app_context():
        get_custom_config().controllers.post_task.max_inline_content = 16
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 413
    assert not relayed(backend)


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


@pytest.mark.parametrize(
    "task",
    [
        TASK_CREDENTIALS,
        {**TASK, "outputs": [{"url": "s3://key@bucket/out", "path": "/out"}]},
    ],
)
def test_create_task_url_credentials_rejected(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    task: dict[str, Any],
) -> None:
    """Tasks with credentials in URLs are rejected (`400`), not stored (S10)."""
    response = client.post(f"{BASE_PATH}/tasks", json=task)
    assert response.status_code == 400
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


def test_create_task_url_credentials_stripped(
    app: FlaskApp,
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Credentials in URLs are removed (`strip`): never stored or relayed.

    Inputs with content are kept.
    """
    with app.app.app_context():
        get_custom_config().controllers.post_task.url_credentials = "strip"
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
    document = stored(mongo_client, response.get_json()["id"])
    for field in ("task", "task_original"):
        assert document[field]["inputs"][0]["url"] == "ftp://ftp.example.org/in"
    assert "secret" not in str(document)


def test_create_task_url_credentials_middleware(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials that middlewares add to URLs are removed, too."""
    apply = TaskDistributionRandom.apply_middleware

    def add_credentials(self: TaskDistributionRandom, request: Any) -> Any:
        """Add credentials to the URL of the input."""
        request = apply(self, request)
        request.json["inputs"][0]["url"] = "s3://key:secret@bucket/in"
        return request

    monkeypatch.setattr(TaskDistributionRandom, "apply_middleware", add_credentials)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES)
    assert response.status_code == 200
    ((_, task),) = relayed(backend)
    assert task.inputs[0].url == "s3://bucket/in"
    assert "secret" not in str(stored(mongo_client, response.get_json()["id"]))


@pytest.mark.parametrize("mode", ["reject", "strip"])
def test_handle_url_credentials_malformed(mode: Literal["reject", "strip"]) -> None:
    """Tasks with malformed URLs are rejected (`400`).

    The API's request validation rejects them first.
    """
    payload = {"inputs": [{"url": "https://[invalid/in", "path": "/in"}]}
    with pytest.raises(BadRequest, match="Malformed URL in `inputs`"):
        task_runs.TaskRuns._handle_url_credentials(payload=payload, mode=mode)


def test_handle_url_credentials_skipped() -> None:
    """Inputs and outputs that are not objects with URLs are left as they are.

    They are rejected by the validation of the task.
    """
    payload: dict[str, Any] = {
        "inputs": [None, {"url": 1}, {"content": "hello"}],
        "outputs": {"url": "https://user@example.org"},
    }
    expected = deepcopy(payload)
    task_runs.TaskRuns._handle_url_credentials(payload=payload, mode="reject")
    assert payload == expected


def test_create_task_payload_per_backend(
    client: FlaskClient,
    backend: type[FakeBackend],
    in_order: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each backend gets the task as validated, without changes for others.

    The first backend changes the task it gets, and rejects it; the next one
    gets it unchanged.
    """
    create = backend.create_task

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Change the task, and reject it, at the first backend."""
        if self.url == BACKENDS[0]:
            task.inputs[0].url = "s3://changed/in"
            raise requests.HTTPError("500")
        return create(self, task)

    monkeypatch.setattr(backend, "create_task", create_task)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES)
    assert response.status_code == 200
    ((url, task),) = relayed(backend)
    assert url == BACKENDS[1]
    assert task.inputs[0].url == "s3://bucket/in"


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
    # so are the logs
    response = client.post(f"{BASE_PATH}/tasks", json={**TASK, "logs": [{}]})
    assert response.status_code == 400
    before = datetime.now(UTC)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    after = datetime.now(UTC)
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


def test_cancel_task_backend_not_configured(
    app: FlaskApp,
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks of backends that are no longer configured can't be canceled."""
    set_state(mongo_client, task_id, "RUNNING")
    with app.app.app_context():
        get_custom_config().tes.service_list = []
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 500
    assert response.get_json()["detail"] == (
        "The TES instance of the task is not configured."
    )
    assert backend.canceled == []
    assert stored(mongo_client, task_id)["task"]["state"] == "RUNNING"


def test_cancel_task_backend_auth_error(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks can't be canceled without credentials for their backend."""

    def cancel_task(self: FakeBackend, task_id: str) -> None:
        """Fail, as if no token was obtained."""
        raise BackendAuthError("no token")

    monkeypatch.setattr(backend, "cancel_task", cancel_task)
    set_state(mongo_client, task_id, "RUNNING")
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == 500
    assert "No credentials" in response.get_json()["detail"]
    assert stored(mongo_client, task_id)["task"]["state"] == "RUNNING"


@pytest.mark.parametrize(
    "error, status",
    [(BackendOutcomeUnknown("timed out"), 502), (requests.HTTPError("400"), 500)],
)
def test_cancel_task_backend_error(
    client: FlaskClient,
    task_id: str,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
) -> None:
    """Tasks stay as they are if their backend doesn't confirm the cancel.

    If the backend may have canceled the task, the request fails with `502`.
    """

    def cancel_task(self: FakeBackend, task_id: str) -> None:
        """Fail."""
        raise error

    monkeypatch.setattr(backend, "cancel_task", cancel_task)
    set_state(mongo_client, task_id, "RUNNING")
    response = client.post(f"{BASE_PATH}/tasks/{task_id}:cancel")
    assert response.status_code == status
    assert stored(mongo_client, task_id)["task"]["state"] == "RUNNING"


def test_create_task_no_basic_auth(
    client: FlaskClient, backend: type[FakeBackend], tracked: list[dict[str, Any]]
) -> None:
    """Basic credentials of callers are rejected, never stored or queued (#213)."""
    credentials = base64.b64encode(b"user:secret").decode()
    response = client.post(
        f"{BASE_PATH}/tasks",
        json=TASK,
        headers={"Authorization": f"Basic {credentials}"},
    )
    assert response.status_code == 401
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    ((tracked_task),) = tracked
    assert set(tracked_task) == {
        "worker_id",
        "remote_host",
        "remote_base_path",
        "remote_task_id",
    }


def test_create_task_resources(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Resources are relayed."""
    task = {**TASK, "resources": {"cpu_cores": 2, "ram_gb": 4.0}}
    assert client.post(f"{BASE_PATH}/tasks", json=task).status_code == 200
    ((_, relayed_task),) = relayed(backend)
    assert relayed_task.resources.cpu_cores == 2


def test_create_task_not_relayable(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks that py-tes can't represent fail (`400`); they are not kept."""

    def task(**kwargs: Any) -> tes.Task:
        """Reject the task, as py-tes does with unknown fields."""
        raise TypeError("unexpected keyword argument")

    monkeypatch.setattr(task_runs.tes, "Task", task)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 400
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


def test_create_task_no_body(app: FlaskApp, backend: type[FakeBackend]) -> None:
    """Requests without a JSON body are rejected (connexion rejects them first)."""
    with (
        app.app.test_request_context(data="null", content_type="application/json"),
        pytest.raises(BadRequest, match="body is missing"),
    ):
        task_runs.TaskRuns().create_task()


def test_create_task_body_removed(
    client: FlaskClient, backend: type[FakeBackend], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Middlewares that remove the request body fail the request (`500`)."""
    monkeypatch.setattr(
        MiddlewareHandler,
        "apply_middlewares",
        lambda self, request: SimpleNamespace(json=None),
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    assert not relayed(backend)


def test_invalid_view(app: FlaskApp) -> None:
    """Invalid views are rejected (connexion rejects them first)."""
    with app.app.app_context(), pytest.raises(BadRequest):
        task_runs.TaskRuns()._set_projection(view="ALL")


def test_task_id_taken(
    app: FlaskApp,
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Task identifiers are generated until one is unused, or attempts run out."""
    with app.app.test_request_context():
        runs = task_runs.TaskRuns()
        attempts = runs.custom_config.controllers.post_task.db.insert_attempts
        inserted: list[str] = []

        def insert_one(document: dict[str, Any]) -> None:
            """Insert the document; all but the last attempt fail."""
            inserted.append(document["task"]["id"])
            if len(inserted) < attempts:
                raise DuplicateKeyError("taken")

        def insert_none(document: dict[str, Any]) -> None:
            """Fail to insert the document."""
            raise DuplicateKeyError("taken")

        monkeypatch.setattr(runs.db_client, "insert_one", insert_one)
        task_id, _ = runs._write_doc_to_db(
            document=DbDocument(), owner=USER, collection=f"{BASE_PATH}/tasks"
        )
        assert inserted[-1] == task_id
        assert len(inserted) == attempts
        # only the stored task keeps its owner
        rules = mongo_client[DB]["policy_rules"].find({"ptype": "g2"})
        assert sorted(rule["v2"] for rule in rules) == [
            f"{BASE_PATH}/tasks/{task_id}",
            f"{BASE_PATH}/tasks/{task_id}:cancel",
        ]
        monkeypatch.setattr(runs.db_client, "insert_one", insert_none)
        with pytest.raises(DuplicateKeyError, match="unused task identifier"):
            runs._write_doc_to_db(
                document=DbDocument(), owner=USER, collection=f"{BASE_PATH}/tasks"
            )


@pytest.mark.parametrize("other_owner", [OTHER_USER, USER])
def test_task_id_taken_concurrently(
    app: FlaskApp,
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
    other_owner: str,
) -> None:
    """Tasks are not stored under identifiers stored concurrently (#248).

    Another request stores a task with the same identifier after the
    identifier was checked: the unique index rejects the task, which gets
    another identifier. Its owner never gets a role on the first
    identifier, also not while the task is stored.
    """
    ids = iter(["TAKEN1", "UNUSED"])
    monkeypatch.setattr(task_runs, "generate_id", lambda **kwargs: next(ids))
    tasks = mongo_client[DB]["tasks"]
    rules = mongo_client[DB]["policy_rules"]
    resources = {
        task: [f"{BASE_PATH}/tasks/{task}", f"{BASE_PATH}/tasks/{task}:cancel"]
        for task in ("TAKEN1", "UNUSED")
    }
    owners_at_insert: list[list[str]] = []

    with app.app.test_request_context():
        runs = task_runs.TaskRuns()
        insert_one = runs.db_client.insert_one

        def insert_one_raced(document: dict[str, Any]) -> Any:
            """Store another task with the identifier first, then the task.

            Args:
                document: Document to insert.

            Returns:
                Result of the insert.
            """
            task_id = document["task"]["id"]
            if task_id == "TAKEN1":
                insert_one(
                    {"task": {"id": task_id}, "user_id": other_owner, "worker_id": "w"}
                )
                ownership.add_task_owner(
                    collection=f"{BASE_PATH}/tasks", task_id=task_id, owner=other_owner
                )
            query = {"ptype": "g2", "v2": resources[task_id][0]}
            owners_at_insert.append([rule["v0"] for rule in rules.find(query)])
            return insert_one(document)

        monkeypatch.setattr(runs.db_client, "insert_one", insert_one_raced)
        task_id, _ = runs._write_doc_to_db(
            document=DbDocument(user_id=USER),
            owner=USER,
            collection=f"{BASE_PATH}/tasks",
        )
    assert task_id == "UNUSED"
    assert owners_at_insert == [[f"user:{other_owner}"], []]
    assert sorted(
        (document["task"]["id"], document["user_id"]) for document in tasks.find()
    ) == [("TAKEN1", other_owner), ("UNUSED", USER)]
    assert sorted(
        (rule["v0"], rule["v2"]) for rule in rules.find({"ptype": "g2"})
    ) == sorted(
        (f"user:{owner}", resource)
        for owner, task in [(other_owner, "TAKEN1"), (USER, "UNUSED")]
        for resource in resources[task]
    )


def test_forward_stored_no_logs(app: FlaskApp) -> None:
    """Forwards of tasks without logs can't be stored."""
    connector = SimpleNamespace(
        upsert_fields_in_root_object=lambda **kwargs: DbDocument()
    )
    with app.app.app_context(), pytest.raises(ValueError, match="has no logs"):
        task_runs.TaskRuns()._update_doc_in_db(
            db_connector=connector,  # type: ignore[arg-type]
            tes_url=BACKENDS[0],
            remote_task_id="remote-1",
        )


def test_task_not_stored(
    app: FlaskApp,
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """The owner is revoked if the task can't be stored."""

    def insert_one(document: dict[str, Any]) -> None:
        """Fail to insert the document.

        Raises:
            pymongo.errors.PyMongoError: Always.
        """
        raise PyMongoError("unavailable")

    with app.app.test_request_context():
        runs = task_runs.TaskRuns()
        monkeypatch.setattr(runs.db_client, "insert_one", insert_one)
        with pytest.raises(PyMongoError, match="unavailable"):
            runs._write_doc_to_db(
                document=DbDocument(), owner=USER, collection=f"{BASE_PATH}/tasks"
            )
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


def test_task_owner_partly_granted(
    app: FlaskApp,
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Owner roles granted partly are revoked, and the task is not stored."""
    add_owner = ownership.add_owner
    granted: list[str] = []

    def add_owner_once(resource: str, subject: str) -> None:
        """Grant the first owner role only.

        Args:
            resource: Resource of the role.
            subject: Owner.

        Raises:
            pymongo.errors.PyMongoError: For the second role.
        """
        if granted:
            raise PyMongoError("unavailable")
        granted.append(resource)
        add_owner(resource=resource, subject=subject)

    monkeypatch.setattr(ownership, "add_owner", add_owner_once)
    with app.app.test_request_context():
        runs = task_runs.TaskRuns()
        with pytest.raises(PyMongoError, match="unavailable"):
            runs._write_doc_to_db(
                document=DbDocument(), owner=USER, collection=f"{BASE_PATH}/tasks"
            )
    assert granted
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
