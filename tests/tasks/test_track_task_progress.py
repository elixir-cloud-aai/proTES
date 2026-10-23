"""Tests of the Celery task tracking tasks at TES backends."""

from collections.abc import Iterator
from typing import Any, ClassVar

from connexion import FlaskApp
from foca import get_collection
from pymongo import MongoClient
from pymongo.collection import Collection
import pytest
import requests
import tes

from pro_tes.app import init_app
from pro_tes.config_models import Discovery, TesBackend
from pro_tes.exceptions import BackendNotConfigured
from pro_tes.ga4gh.tes import backends, discovery
from pro_tes.tasks import track_task_progress
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.utils.misc import get_custom_config
from tests.conftest import DB, OTHER_DB

# Arguments of the Celery task
ARGS: dict[str, Any] = {
    "worker_id": "worker-1",
    "remote_host": "https://tes.example.org/",
    "remote_base_path": "/api/",
    "remote_task_id": "remote-1",
}
OUTPUT = tes.OutputFileLog(url="s3://bucket/out", path="/out", size_bytes=10)


class FakeClient:
    """TES client of a backend serving a task with a sequence of states.

    States are served in turn, the last one repeatedly; `None` stands for a
    failed request.

    Attributes:
        url: URL of the backend.
        backend: Configuration of the backend.
    """

    states: ClassVar[list[str | None]] = ["COMPLETE"]
    outputs: ClassVar[list[tes.OutputFileLog]] = [OUTPUT]
    # Clients created, and requested task identifiers
    clients: ClassVar[list["FakeClient"]] = []
    requested: ClassVar[list[str]] = []

    def __init__(self, url: str, backend: TesBackend) -> None:
        """Create the client."""
        self.url = url
        self.backend = backend
        self.clients.append(self)

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get the task, in its next state, with one log.

        Raises:
            requests.HTTPError: The state is `None`.
        """
        self.requested.append(task_id)
        state = self.states[0]
        if len(self.states) > 1:
            self.states.pop(0)
        if state is None:
            raise requests.HTTPError("503")
        return tes.Task(
            id=task_id,
            state=state,
            logs=[tes.TaskLog(logs=[], outputs=self.outputs)],
        )


@pytest.fixture
def client_class(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    """Fake TES client, whose task is complete."""
    monkeypatch.setattr(FakeClient, "states", ["COMPLETE"])
    monkeypatch.setattr(FakeClient, "outputs", [OUTPUT])
    monkeypatch.setattr(FakeClient, "clients", [])
    monkeypatch.setattr(FakeClient, "requested", [])
    monkeypatch.setattr(backends, "TesClient", FakeClient)
    return FakeClient


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Waiting times between polls (without waiting)."""
    calls: list[float] = []
    monkeypatch.setattr(track_task_progress, "sleep", calls.append)
    return calls


def insert_task(collection: Collection[dict[str, Any]]) -> None:
    """Store a queued task, as the controller does."""
    collection.insert_one(
        {
            "worker_id": ARGS["worker_id"],
            "task": {
                "id": "TASK01",
                "state": "QUEUED",
                "executors": [],
                "logs": [{"logs": [], "outputs": []}],
            },
        }
    )


@pytest.fixture
def tasks(app: FlaskApp) -> Iterator[Collection[dict[str, Any]]]:
    """Collection of the tasks, with a queued task; in an app context."""
    with app.app.app_context():
        collection = get_collection(DB, "tasks")
        insert_task(collection)
        yield collection


@pytest.fixture
def stored_states(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """States stored by the Celery task, in order."""
    calls: list[str] = []
    update = DbDocumentConnector.update_task_state

    def record(self: DbDocumentConnector, state: str = "UNKNOWN") -> None:
        """Record the state, and store it."""
        calls.append(state)
        update(self, state=state)

    monkeypatch.setattr(DbDocumentConnector, "update_task_state", record)
    return calls


def run() -> None:
    """Run the Celery task (in the current app context)."""
    track_task_progress.task__track_task_progress.run(**ARGS)


@pytest.mark.parametrize("db_name", [None, OTHER_DB])
def test_task_tracked(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
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
    collection = mongo_client[db_name or DB]["tasks"]
    insert_task(collection)
    with app.app.app_context():
        run()
    document = collection.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "COMPLETE"
    assert document["task"]["logs"][0]["outputs"] == [
        {"url": "s3://bucket/out", "path": "/out", "size_bytes": "10"}
    ]


def test_output_credentials_removed(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
) -> None:
    """Credentials in the URLs of outputs that backends log aren't stored.

    E.g., of tasks that earlier versions forwarded with credentials; URLs
    that can't be parsed are stored as logged.
    """
    client_class.outputs = [
        tes.OutputFileLog(url="s3://user:pw@bucket/out", path="/out", size_bytes=1),
        tes.OutputFileLog(url="https://[bad/out", path="/bad", size_bytes=1),
    ]
    run()
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    urls = [output["url"] for output in document["task"]["logs"][0]["outputs"]]
    assert urls == ["s3://bucket/out", "https://[bad/out"]


def test_backend_client(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
) -> None:
    """The backend is called at its URL, with its configuration (credentials)."""
    run()
    (cli,) = client_class.clients
    assert cli.url == "https://tes.example.org/api"
    assert cli.backend == TesBackend(url="https://tes.example.org/", auth="none")
    assert set(client_class.requested) == {"remote-1"}


def test_backend_not_configured(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tasks fail (`SYSTEM_ERROR`) if their backend is no longer configured."""
    get_custom_config().tes.service_list = []
    with pytest.raises(BackendNotConfigured):
        run()
    assert stored_states == ["INITIALIZING", "SYSTEM_ERROR"]
    assert not client_class.clients


class Retried(Exception):
    """Raised instead of retrying the Celery task."""


@pytest.mark.parametrize("retries, retried", [(0, True), (3, False)])
def test_backend_unknown_retried(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
    retried: bool,
) -> None:
    """With discovery, tasks of unknown backends are retried, then fail.

    The worker may not know a backend that another process discovered yet.
    """
    task = track_task_progress.task__track_task_progress
    calls: list[dict[str, Any]] = []

    def retry(**kwargs: Any) -> Retried:
        """Record the retry."""
        calls.append(kwargs)
        return Retried()

    monkeypatch.setattr(task, "retry", retry)
    monkeypatch.setattr(task.request, "retries", retries, raising=False)
    tes_config = get_custom_config().tes
    tes_config.service_list = []
    tes_config.discovery = Discovery(
        registry_url="https://registry.example.org/api", auth="none"
    )
    with pytest.raises(Retried if retried else BackendNotConfigured):
        run()
    if retried:
        assert stored_states == ["INITIALIZING"]
        assert calls[0]["countdown"] == discovery.MIN_REFRESH_INTERVAL
        assert isinstance(calls[0]["exc"], BackendNotConfigured)
    else:
        assert stored_states == ["INITIALIZING", "SYSTEM_ERROR"]
        assert not calls


def test_states_tracked(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Changes of the state are stored until the task is finished.

    The backend is polled at the configured interval.
    """
    client_class.states = [
        "QUEUED",
        "QUEUED",
        "RUNNING",
        "RUNNING",
        "EXECUTOR_ERROR",
    ]
    run()
    assert stored_states == ["INITIALIZING", "QUEUED", "RUNNING", "EXECUTOR_ERROR"]
    # the first request is not preceded by a wait
    assert sleeps == [3] * (len(client_class.requested) - 1)
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "EXECUTOR_ERROR"


def test_preempted_final(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tracking ends once the backend reports `PREEMPTED` (TES 1.1).

    The final logs are stored; a state reported after `PREEMPTED` would be
    tracked only if the tracker kept polling.
    """
    client_class.states = ["RUNNING", "RUNNING", "PREEMPTED", "COMPLETE"]
    run()
    assert stored_states == ["INITIALIZING", "RUNNING", "PREEMPTED"]
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "PREEMPTED"
    assert document["task"]["logs"][0]["outputs"] == [
        {"url": "s3://bucket/out", "path": "/out", "size_bytes": "10"}
    ]


def test_canceling_tracked(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tasks in `CANCELING` (TES 1.1) are tracked until they are `CANCELED`."""
    client_class.states = [
        "RUNNING",
        "RUNNING",
        "CANCELING",
        "CANCELING",
        "CANCELED",
    ]
    run()
    assert stored_states == ["INITIALIZING", "RUNNING", "CANCELING", "CANCELED"]
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "CANCELED"


def test_canceling_poll_attempts_exceeded(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tasks in `CANCELING` fail like others once polls fail too often."""
    attempts = get_custom_config().controllers.post_task.polling.attempts
    client_class.states = ["RUNNING", "CANCELING", None]
    with pytest.raises(requests.HTTPError):
        run()
    assert stored_states == ["INITIALIZING", "CANCELING", "SYSTEM_ERROR"]
    # the first two requests, the allowed failed attempts, and the last failure
    assert len(client_class.requested) == attempts + 3


def test_poll_retried(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Failed polls are retried."""
    client_class.states = ["QUEUED", None, None, "COMPLETE"]
    run()
    assert stored_states == ["INITIALIZING", "COMPLETE"]


def test_poll_attempts_exceeded(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tasks fail (`SYSTEM_ERROR`) once polls fail more often than allowed."""
    attempts = get_custom_config().controllers.post_task.polling.attempts
    client_class.states = ["QUEUED", None]
    with pytest.raises(requests.HTTPError):
        run()
    assert stored_states == ["INITIALIZING", "SYSTEM_ERROR"]
    # the first request, the allowed failed attempts, and the last failure
    assert len(client_class.requested) == attempts + 2


def test_backend_unavailable(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
    stored_states: list[str],
) -> None:
    """Tasks fail (`SYSTEM_ERROR`) if the backend doesn't serve them at first."""
    client_class.states = [None, "COMPLETE"]
    with pytest.raises(requests.HTTPError):
        run()
    assert stored_states == ["INITIALIZING", "SYSTEM_ERROR"]
    assert not sleeps


def test_logs_missing(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    sleeps: list[float],
) -> None:
    """Tasks without logs at the backend fail to update."""
    tasks.update_one({"worker_id": ARGS["worker_id"]}, {"$unset": {"task.logs": ""}})
    with pytest.raises(ValueError, match="Task has no logs"):
        run()


def test_document_missing(app: FlaskApp) -> None:
    """Updates of missing documents fail."""
    with app.app.app_context():
        connector = DbDocumentConnector(
            collection=get_collection(DB, "tasks"), worker_id="none"
        )
        with pytest.raises(ValueError, match="No database document"):
            connector.upsert_fields_in_root_object(root="task", state="QUEUED")
