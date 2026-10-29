"""Tests of the Celery tasks tracking tasks at TES backends."""

from collections.abc import Iterator
from datetime import UTC, datetime
import time
from typing import Any, ClassVar

from bson.objectid import ObjectId
from celery.exceptions import Reject
from connexion import FlaskApp
from foca import get_collection, get_custom_config
from foca.models.config import Config
from kombu.exceptions import OperationalError
from pydantic import ValidationError
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import ServerSelectionTimeoutError
import pytest
import requests
import tes

from pro_tes.app import init_app
from pro_tes.celery_worker import celery
from pro_tes.config_models import CustomConfig, Discovery, Polling, TesBackend
from pro_tes.ga4gh.tes import backends
from pro_tes.tasks import track_task_progress
from pro_tes.utils.db import DbDocumentConnector
from tests.conftest import DB, OTHER_DB

# Arguments of the Celery task
ARGS: dict[str, Any] = {
    "worker_id": "worker-1",
    "remote_host": "https://tes.example.org/",
    "remote_base_path": "/api/",
    "remote_task_id": "remote-1",
}
OUTPUT = tes.OutputFileLog(url="s3://bucket/out", path="/out", size_bytes=10)
TRACK = track_task_progress.task__track_task_progress


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
def scheduled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Polls scheduled by the Celery tasks (not run).

    Each with its arguments (`kwargs`) and `countdown`, in order.
    """
    calls: list[dict[str, Any]] = []

    def apply_async(**kwargs: Any) -> None:
        """Record the scheduled poll."""
        calls.append(kwargs)

    monkeypatch.setattr(TRACK, "apply_async", apply_async)
    return calls


def insert_task(
    collection: Collection[dict[str, Any]],
    worker_id: str = ARGS["worker_id"],
    state: str = "QUEUED",
    **fields: Any,
) -> None:
    """Store a task, by default a queued one, as the controller does.

    Args:
        collection: Database collection of the tasks.
        worker_id: Worker identifier of the task.
        state: State of the task.
        **fields: Further fields of the document.
    """
    collection.insert_one(
        {
            "worker_id": worker_id,
            "task": {
                "id": f"TASK-{worker_id}",
                "state": state,
                "executors": [],
                "logs": [{"logs": [], "outputs": []}],
            },
            **fields,
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
    """States stored by the Celery task, in order (changes only)."""
    calls: list[str] = []
    store = track_task_progress._store_state

    def record(
        collection: Collection[dict[str, Any]], worker_id: str, **kwargs: Any
    ) -> None:
        """Store the state, and record it if it was stored."""
        before = collection.find_one({"worker_id": worker_id})
        store(collection=collection, worker_id=worker_id, **kwargs)
        after = collection.find_one({"worker_id": worker_id})
        assert before is not None and after is not None
        if after["task"]["state"] != before["task"]["state"]:
            calls.append(after["task"]["state"])

    monkeypatch.setattr(track_task_progress, "_store_state", record)
    return calls


@pytest.fixture
def polling(app: FlaskApp) -> Polling:
    """Polling configuration of the app (changed by tests)."""
    return get_custom_config(CustomConfig).controllers.post_task.polling


def run(**kwargs: Any) -> None:
    """Run one poll of the Celery task (in the current app context).

    Args:
        **kwargs: Arguments that replace those of `ARGS` or are added.
    """
    TRACK.run(**{**ARGS, **kwargs})


def track(scheduled: list[dict[str, Any]], limit: int = 50) -> list[int]:
    """Track the task: run the first poll, and then each scheduled one.

    Args:
        scheduled: Polls scheduled, as recorded by fixture `scheduled`.
        limit: Maximum number of polls.

    Returns:
        Seconds until each scheduled poll (`countdown`), in order.
    """
    countdowns: list[int] = []
    run()
    for _ in range(limit):
        if not scheduled:
            return countdowns
        call = scheduled.pop(0)
        assert not scheduled, "more than one poll scheduled"
        countdowns.append(call["countdown"])
        TRACK.run(**call["kwargs"])
    pytest.fail("tracking didn't end")  # pragma: no cover


def current_poll(collection: Collection[dict[str, Any]]) -> str | None:
    """Get the identifier of the poll tracking the task of `ARGS`.

    Args:
        collection: Database collection of the tasks.

    Returns:
        Identifier, or `None` if there is none.
    """
    document = collection.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    return (document.get("tracking") or {}).get("poll")


@pytest.mark.parametrize("db_name", [None, OTHER_DB])
def test_task_tracked(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
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
    assert not scheduled


def test_output_credentials_removed(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
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
    scheduled: list[dict[str, Any]],
) -> None:
    """The backend is called at its URL, with its configuration (credentials)."""
    run()
    (cli,) = client_class.clients
    assert cli.url == "https://tes.example.org/api"
    assert cli.backend == TesBackend(url="https://tes.example.org/", auth="none")
    assert set(client_class.requested) == {"remote-1"}


@pytest.mark.parametrize("discovered", [False, True])
def test_backend_not_configured(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
    polling: Polling,
    discovered: bool,
) -> None:
    """Polls of tasks whose backend is unknown fail like other polls.

    E.g., backends no longer configured, or not discovered (yet, or for the
    time being): the task may still run there, so it doesn't fail, and is
    tracked again once its backend is known.
    """
    polling.attempts = 2
    tes_config = get_custom_config(CustomConfig).tes
    tes_config.service_list = []
    if discovered:
        tes_config.discovery = Discovery(
            registry_url="https://registry.example.org/api", auth="none"
        )
    run()
    run(**scheduled.pop()["kwargs"])
    assert stored_states == ["UNKNOWN"]
    assert not client_class.clients
    (call,) = scheduled
    assert call["kwargs"]["failures"] == 2


class Retried(Exception):
    """Raised instead of retrying the Celery task."""


@pytest.fixture
def retries(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Retries of the Celery task (not run), with their arguments."""
    calls: list[dict[str, Any]] = []

    def retry(**kwargs: Any) -> Retried:
        """Record the retry."""
        calls.append(kwargs)
        return Retried()

    monkeypatch.setattr(TRACK, "retry", retry)
    return calls


@pytest.mark.parametrize("retried, countdown", [(0, 3), (2, 12), (10**6, 600)], ids=str)
def test_database_error_retried(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    retries: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    retried: int,
    countdown: int,
) -> None:
    """Polls are retried if MongoDB is unavailable, with backoff.

    Instead of failing, which would end the tracking of the task.
    """

    def unavailable(**kwargs: Any) -> bool:
        """Fail like an unavailable MongoDB."""
        raise ServerSelectionTimeoutError("unavailable")

    monkeypatch.setattr(track_task_progress, "_claim", unavailable)
    monkeypatch.setattr(TRACK.request, "retries", retried, raising=False)
    with pytest.raises(Retried):
        run()
    (call,) = retries
    assert call["countdown"] == countdown
    assert isinstance(call["exc"], ServerSelectionTimeoutError)
    assert not scheduled


def test_infrastructure_error_not_failed_poll(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    retries: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors of MongoDB while polling don't count as failed polls.

    The poll is retried instead.
    """

    def unavailable(self: FakeClient, task_id: str, **kwargs: Any) -> tes.Task:
        """Fail like an unavailable MongoDB."""
        raise ServerSelectionTimeoutError("unavailable")

    monkeypatch.setattr(client_class, "get_task", unavailable)
    with pytest.raises(Retried):
        run()
    assert len(retries) == 1
    assert not scheduled


def test_retry_not_published_requeued(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polls go back to the broker if their retry can't be published.

    Celery would reject them without requeuing them, which ends the chain.
    """

    def unavailable(**kwargs: Any) -> bool:
        """Fail like an unavailable MongoDB."""
        raise ServerSelectionTimeoutError("unavailable")

    def retry(**kwargs: Any) -> None:
        """Fail to publish the retry, as Celery does."""
        raise Reject(OperationalError("unavailable"), requeue=False)

    monkeypatch.setattr(track_task_progress, "_claim", unavailable)
    monkeypatch.setattr(TRACK, "retry", retry)
    with pytest.raises(Reject) as info:
        run()
    reason, requeue = info.value.args
    assert requeue
    assert isinstance(reason, OperationalError)


def test_broker_error_requeued(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    retries: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polls go back to the broker if the next poll can't be scheduled.

    As a retry couldn't be published either; the poll then claims the
    tracking again, as it still holds it.
    """
    client_class.states = ["RUNNING"]

    def unavailable(**kwargs: Any) -> None:
        """Fail like an unavailable broker."""
        raise OperationalError("unavailable")

    monkeypatch.setattr(TRACK, "apply_async", unavailable)
    with pytest.raises(Reject) as info:
        run()
    reason, requeue = info.value.args
    assert requeue
    assert isinstance(reason, OperationalError)
    assert not retries
    assert current_poll(tasks) is None


def test_states_tracked(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """Changes of the state are stored until the task is finished.

    Each run of the Celery task polls the backend once, and schedules the
    next poll after the configured interval, instead of blocking a worker
    process for the runtime of the task.
    """
    client_class.states = [
        "QUEUED",
        "QUEUED",
        "RUNNING",
        "RUNNING",
        "EXECUTOR_ERROR",
    ]
    assert track(scheduled) == [3, 3, 3, 3]
    # the task was stored as queued
    assert stored_states == ["RUNNING", "EXECUTOR_ERROR"]
    assert len(client_class.requested) == 5
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "EXECUTOR_ERROR"


def test_preempted_final(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """Tracking ends once the backend reports `PREEMPTED` (TES 1.1).

    The final logs are stored; a state reported after `PREEMPTED` would be
    tracked only if the tracker kept polling.
    """
    client_class.states = ["RUNNING", "RUNNING", "PREEMPTED", "COMPLETE"]
    track(scheduled)
    assert stored_states == ["RUNNING", "PREEMPTED"]
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "PREEMPTED"
    assert document["task"]["logs"][0]["outputs"] == [
        {"url": "s3://bucket/out", "path": "/out", "size_bytes": "10"}
    ]


def test_canceling_tracked(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """Tasks in `CANCELING` (TES 1.1) are tracked until they are `CANCELED`."""
    client_class.states = [
        "RUNNING",
        "RUNNING",
        "CANCELING",
        None,
        "CANCELING",
        "CANCELED",
    ]
    track(scheduled)
    assert stored_states == ["RUNNING", "CANCELING", "CANCELED"]
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == "CANCELED"


@pytest.mark.parametrize("final", ["CANCELED", "COMPLETE"])
def test_cancelation_kept(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
    polling: Polling,
    final: str,
) -> None:
    """A stored cancelation is kept until the backend has finished the task.

    Backends cancel tasks asynchronously, and may report them as running (or
    be unreachable) for a while; the final state is stored, also if the task
    completed anyway.
    """
    polling.attempts = 1
    tasks.update_one({}, {"$set": {"task.state": "CANCELED"}})
    client_class.states = ["RUNNING", None, "CANCELING", final]
    track(scheduled)
    assert stored_states == ([] if final == "CANCELED" else [final])
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["task"]["state"] == final


def test_cancelation_not_overwritten(tasks: Collection[dict[str, Any]]) -> None:
    """States of polls don't overwrite a cancelation stored meanwhile.

    E.g., if the task is canceled after the poll read its state; final
    states do.
    """
    tasks.update_one({}, {"$set": {"task.state": "CANCELED"}})
    track_task_progress._store_state(
        collection=tasks, worker_id=ARGS["worker_id"], state="RUNNING"
    )
    document = tasks.find_one({})
    assert document is not None
    assert document["task"]["state"] == "CANCELED"
    track_task_progress._store_state(
        collection=tasks,
        worker_id=ARGS["worker_id"],
        state="COMPLETE",
        keep_canceled=False,
    )
    document = tasks.find_one({})
    assert document is not None
    assert document["task"]["state"] == "COMPLETE"


def test_first_poll_retried(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """A failed first poll is retried, like the others (no `SYSTEM_ERROR`)."""
    client_class.states = [None, "COMPLETE"]
    assert track(scheduled) == [3]
    assert stored_states == ["COMPLETE"]


def test_consecutive_failures_reset(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
    polling: Polling,
) -> None:
    """Only consecutive failed polls count: a successful poll resets them.

    More polls fail than `attempts` over the life of the task, but never
    `attempts` in a row, so the state is never `UNKNOWN`, and polls don't
    back off.
    """
    polling.attempts = 3
    client_class.states = [None, None, "RUNNING", None, None, "RUNNING"]
    client_class.states += [None, None, "COMPLETE"]
    assert track(scheduled) == [3] * 8
    assert stored_states == ["RUNNING", "COMPLETE"]
    assert len(client_class.requested) == 9


def test_failures_exceeded(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
    polling: Polling,
) -> None:
    """After `attempts` consecutive failures, the state is `UNKNOWN`.

    No final state is set, as the task may still run at its backend: polls
    back off, up to `max_wait` seconds apart, and the state is stored again
    once the backend answers; then polls are `wait` seconds apart again.
    """
    polling.attempts = 3
    polling.max_wait = 20
    client_class.states = [None] * 6 + ["RUNNING", "RUNNING", "COMPLETE"]
    assert track(scheduled) == [3, 3, 6, 12, 20, 20, 3, 3]
    assert stored_states == ["UNKNOWN", "RUNNING", "COMPLETE"]


def test_attempts_lowered(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """Tasks become `UNKNOWN` also if `attempts` was lowered meanwhile.

    I.e., below the failures counted by a scheduled poll.
    """
    client_class.states = [None]
    run(failures=200)
    assert stored_states == ["UNKNOWN"]


def test_failures_resumed(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """Resumed polls continue from the stored count of failed polls.

    So that they keep backing off while the backend is unavailable.
    """
    client_class.states = [None]
    endpoint = {"host": ARGS["remote_host"], "base_path": "", "task_id": "remote-1"}
    tasks.update_one({}, {"$set": {"tes_endpoint": endpoint}})
    run(failures=4)
    document = tasks.find_one({})
    assert document is not None
    assert document["tracking"]["failures"] == 5
    tasks.update_one({}, {"$set": {"tracking.polled_at": 0}})
    scheduled.clear()
    track_task_progress.task__resume_tracking.run()
    (call,) = scheduled
    assert call["kwargs"]["failures"] == 5


def stored_since(seconds: float) -> ObjectId:
    """Get the document identifier of a task stored a while ago.

    Args:
        seconds: Seconds since the task was stored.

    Returns:
        A new document identifier with that time, as MongoDB sets them.
    """
    stored = ObjectId.from_datetime(datetime.fromtimestamp(time.time() - seconds, UTC))
    # the time, and the unique rest of a new identifier
    return ObjectId(stored.binary[:4] + ObjectId().binary[4:])


def test_cut_off_creates_failed(
    tasks: Collection[dict[str, Any]],
    scheduled: list[dict[str, Any]],
) -> None:
    """Tasks whose creation was cut off are set to `SYSTEM_ERROR`.

    I.e., tasks still in state `UNKNOWN` without identifier at a backend,
    stored more than `max_create_time` seconds ago (e.g., if proTES stopped
    while forwarding them), also by earlier versions (without creation
    time); not tasks being created, tasks with a remote identifier (also in
    the logs, of earlier versions), or tasks in other states.
    """
    max_create_time = get_custom_config(
        CustomConfig
    ).controllers.post_task.max_create_time
    old = max_create_time + 60
    endpoint = {"host": ARGS["remote_host"], "base_path": "", "task_id": "remote-1"}
    tasks.delete_many({})

    def insert(worker_id: str, stored: float = old, **fields: Any) -> None:
        """Store a task in state `UNKNOWN`.

        Args:
            worker_id: Worker identifier of the task.
            stored: Seconds since the task was stored.
            **fields: Further fields of the document.
        """
        insert_task(
            tasks, worker_id, state="UNKNOWN", _id=stored_since(stored), **fields
        )

    insert("cut-off")
    insert("cut-off-host", tes_endpoint={"host": ARGS["remote_host"], "base_path": ""})
    insert("cut-off-no-remote-id", tes_endpoint={"host": "x", "task_id": None})
    insert("cut-off-empty-forwards")
    tasks.update_one(
        {"worker_id": "cut-off-empty-forwards"},
        {"$set": {"task.logs.0.metadata": {"forwarded_to": []}}},
    )
    insert("being-created", stored=60)
    insert("forwarded", tes_endpoint=endpoint)
    insert("queued")
    tasks.update_one({"worker_id": "queued"}, {"$set": {"task.state": "QUEUED"}})
    legacy = {
        "legacy-forwards": {"forwarded_to": [{"id": "remote-2", "url": "x"}]},
        "legacy-nested": {"forwarded_to": {"id": "remote-3", "url": "x"}},
        "legacy-remote-id": {"remote_task_id": "remote-4"},
    }
    for worker_id, metadata in legacy.items():
        insert(worker_id)
        tasks.update_one(
            {"worker_id": worker_id}, {"$set": {"task.logs.0.metadata": metadata}}
        )
    assert track_task_progress.task__resume_tracking.run() == 1
    states = {
        document["worker_id"]: document["task"]["state"] for document in tasks.find()
    }
    assert states == {
        **dict.fromkeys(
            [
                "cut-off",
                "cut-off-host",
                "cut-off-no-remote-id",
                "cut-off-empty-forwards",
            ],
            "SYSTEM_ERROR",
        ),
        **dict.fromkeys(["being-created", "forwarded", *legacy], "UNKNOWN"),
        "queued": "QUEUED",
    }
    # the task with a remote identifier is resumed instead
    (call,) = scheduled
    assert call["kwargs"]["worker_id"] == "forwarded"


def test_cut_off_create_logged(
    tasks: Collection[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    """Tasks set to `SYSTEM_ERROR` are counted and logged.

    Not tasks that changed after they were read (e.g., forwarded since, also
    by earlier versions, which log the remote identifier).
    """
    max_create_time = 60
    tasks.delete_many({})
    for worker_id in ("cut-off", "changed", "changed-legacy"):
        insert_task(
            tasks,
            worker_id,
            state="UNKNOWN",
            _id=stored_since(max_create_time + 1),
        )

    class Forwarding:
        """Collection whose tasks `changed*` are forwarded once read.

        Attributes:
            collection: Collection of the tasks.
        """

        def __init__(self, collection: Collection[dict[str, Any]]) -> None:
            """Wrap the collection."""
            self.collection = collection

        def find(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            """Find the tasks, then forward the tasks `changed*`."""
            found = list(self.collection.find(*args, **kwargs))
            self.collection.update_one(
                {"worker_id": "changed"}, {"$set": {"tes_endpoint.task_id": "r-1"}}
            )
            self.collection.update_one(
                {"worker_id": "changed-legacy"},
                {"$set": {"task.logs.0.metadata": {"remote_task_id": "r-2"}}},
            )
            return found

        def update_one(self, *args: Any, **kwargs: Any) -> Any:
            """Update a task."""
            return self.collection.update_one(*args, **kwargs)

    with caplog.at_level("WARNING", logger=track_task_progress.logger.name):
        failed = track_task_progress._fail_cut_off(
            collection=Forwarding(tasks),  # type: ignore[arg-type]
            max_create_time=max_create_time,
        )
    assert failed == 1
    states = {
        document["worker_id"]: document["task"]["state"] for document in tasks.find()
    }
    assert states == {
        "cut-off": "SYSTEM_ERROR",
        "changed": "UNKNOWN",
        "changed-legacy": "UNKNOWN",
    }
    (record,) = [
        record
        for record in caplog.records
        if record.name == track_task_progress.logger.name
    ]
    assert record.getMessage().startswith("[cut-off] SYSTEM_ERROR")
    assert "within 60 seconds" in record.getMessage()


@pytest.mark.parametrize("field", ["wait", "max_wait"])
def test_intervals_below_ack_timeout(field: str) -> None:
    """Intervals are below the broker's acknowledgement timeout.

    Scheduled polls are acknowledged when they run, so RabbitMQ would
    deliver them again before they are due.
    """
    with pytest.raises(ValidationError):
        Polling(**{field: 1800})


def test_backoff_capped(polling: Polling) -> None:
    """Intervals of polls are capped, also after many failures.

    And never shorter than `wait`, even if `max_wait` is.
    """
    assert track_task_progress.backoff(10**6) == polling.max_wait
    polling.max_wait = 1
    assert track_task_progress.backoff(10**6) == polling.wait


def test_superseded_chain_ends(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """Polls whose identifier is no longer current do nothing.

    E.g., of a chain replaced when the tracking was resumed.
    """
    tasks.update_one({}, {"$set": {"tracking.poll": "current"}})
    run(poll="old", previous="older")
    run()
    assert not client_class.requested
    assert not scheduled
    assert current_poll(tasks) == "current"


def test_tracking_passed_on(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """Each poll passes the tracking on to the next, with a new identifier.

    A redelivered message of an earlier poll then does nothing.
    """
    client_class.states = ["RUNNING"]
    run()
    (call,) = scheduled
    successor = call["kwargs"]["poll"]
    assert successor is not None
    assert call["kwargs"]["previous"] is None
    assert current_poll(tasks) == successor
    run()
    assert len(scheduled) == 1
    assert len(client_class.requested) == 1


class Lost(BaseException):
    """Raised when the worker process is lost (e.g., killed)."""


def test_redelivered_after_worker_lost(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polls whose worker is lost are run again, and one chain remains.

    Messages are acknowledged late, so the broker delivers them again if the
    worker is lost, e.g., between scheduling the next poll and passing the
    tracking on to it. The poll then runs again and schedules another one,
    and the first one it scheduled does nothing.
    """
    client_class.states = ["RUNNING"]
    record = TRACK.apply_async

    def lost(**kwargs: Any) -> None:
        """Schedule the poll, and lose the worker."""
        record(**kwargs)
        raise Lost

    monkeypatch.setattr(TRACK, "apply_async", lost)
    with pytest.raises(Lost):
        run()
    assert current_poll(tasks) is None
    monkeypatch.setattr(TRACK, "apply_async", record)
    # delivered again
    run()
    orphan, current = scheduled
    assert current_poll(tasks) == current["kwargs"]["poll"]
    scheduled.clear()
    TRACK.run(**orphan["kwargs"])
    assert not scheduled
    TRACK.run(**current["kwargs"])
    assert len(scheduled) == 1
    assert len(client_class.requested) == 3


def test_previous_token_claimed(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """A poll takes over from the one that scheduled it.

    If that one didn't pass the tracking on (e.g., it was lost, or the next
    poll ran first).
    """
    client_class.states = ["RUNNING"]
    tasks.update_one({}, {"$set": {"tracking.poll": "previous"}})
    run(poll="next", previous="previous")
    (call,) = scheduled
    assert call["kwargs"]["previous"] == "next"
    assert current_poll(tasks) == call["kwargs"]["poll"]


def test_task_deleted(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """Tracking ends if the task was deleted."""
    tasks.delete_many({})
    run()
    assert not client_class.requested
    assert not scheduled


def test_tracking_resumed(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    polling: Polling,
) -> None:
    """The tracking of stale unfinished tasks with a backend is resumed.

    Each gets a new poll, which takes over from the stored one and ends the
    chain of polls that tracked it before (e.g., of a worker that was lost);
    tasks polled recently, finished tasks, tasks that weren't forwarded and
    tasks without remote identifier (of earlier versions) are not resumed.
    """
    endpoint = {"host": ARGS["remote_host"], "base_path": "", "task_id": "remote-1"}
    stale = time.time() - 3 * polling.max_wait - 1
    tasks.update_one(
        {},
        {"$set": {"tes_endpoint": endpoint, "tracking": {"polled_at": stale}}},
    )
    insert_task(
        tasks, "recent", tes_endpoint=endpoint, tracking={"polled_at": time.time()}
    )
    insert_task(tasks, "complete", state="COMPLETE", tes_endpoint=endpoint)
    insert_task(tasks, "not-forwarded", state="UNKNOWN")
    insert_task(
        tasks,
        "legacy",
        state="RUNNING",
        tes_endpoint={"host": ARGS["remote_host"], "base_path": ""},
    )
    insert_task(tasks, "no-host", tes_endpoint={**endpoint, "host": ""})
    assert track_task_progress.task__resume_tracking.run() == 1
    (call,) = scheduled
    assert call["kwargs"] == {
        "worker_id": ARGS["worker_id"],
        "remote_host": ARGS["remote_host"],
        "remote_base_path": "",
        "remote_task_id": "remote-1",
        "poll": current_poll(tasks),
        "previous": None,
        "failures": 0,
    }
    assert call.get("countdown") is None
    # the first poll of the earlier chain does nothing
    scheduled.clear()
    run()
    assert not scheduled
    # the resumed one tracks the task
    TRACK.run(**call["kwargs"])
    assert client_class.requested == ["remote-1"]


def test_resumed_poll_takes_over(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """A resumed poll takes over from the stored one, also if storing fails.

    E.g., if the worker resuming the tracking is lost before it stored the
    new poll; the chain that tracked the task before then ends.
    """
    client_class.states = ["RUNNING"]
    endpoint = {"host": ARGS["remote_host"], "base_path": "", "task_id": "remote-1"}
    tasks.update_one(
        {}, {"$set": {"tes_endpoint": endpoint, "tracking": {"poll": "stored"}}}
    )
    track_task_progress.task__resume_tracking.run()
    (call,) = scheduled
    assert call["kwargs"]["previous"] == "stored"
    # as if the new poll had not been stored
    tasks.update_one({}, {"$set": {"tracking.poll": "stored"}})
    scheduled.clear()
    TRACK.run(**call["kwargs"])
    assert len(scheduled) == 1
    run(poll="stored", previous="older")
    assert len(scheduled) == 1


def test_poll_time_stored(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
) -> None:
    """Polls store when they claimed the tracking, to find stale tasks."""
    before = time.time()
    run()
    document = tasks.find_one({"worker_id": ARGS["worker_id"]})
    assert document is not None
    assert document["tracking"]["polled_at"] >= before


class FakeThread:
    """Thread that is not started.

    Attributes:
        kwargs: Arguments of its target.
        started: Whether it was started.
    """

    def __init__(self, target: Any, kwargs: dict[str, Any], **options: Any) -> None:
        """Create the thread."""
        self.kwargs = kwargs
        self.started = False

    def start(self) -> None:
        """Start the thread (do nothing)."""
        self.started = True


def test_tracking_resumed_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tracking is resumed when a worker starts, and then periodically.

    Every `polling.max_wait` seconds; also without the custom configuration
    of proTES (the worker doesn't start without it).
    """
    calls: list[bool] = []
    monkeypatch.setattr(
        track_task_progress.task__resume_tracking,
        "delay",
        lambda: calls.append(True),
    )
    monkeypatch.setattr(track_task_progress, "Thread", FakeThread)
    thread = track_task_progress.resume_tracking_on_start(sender=None)
    assert calls == [True]
    assert isinstance(thread, FakeThread)
    assert thread.started
    assert thread.kwargs["interval"] == 600
    monkeypatch.setitem(celery.conf, "foca", Config())
    thread = track_task_progress.resume_tracking_on_start(sender=None)
    assert isinstance(thread, FakeThread)
    assert thread.kwargs["interval"] == Polling().max_wait


class Stop:
    """Event that is set after a number of waits.

    Attributes:
        waits: Intervals waited for.
    """

    def __init__(self, waits: int) -> None:
        """Create the event."""
        self.remaining = waits
        self.waits: list[float] = []

    def wait(self, timeout: float) -> bool:
        """Wait (without waiting).

        Returns:
            Whether the event is set.
        """
        self.waits.append(timeout)
        self.remaining -= 1
        return self.remaining < 0


def test_tracking_resumed_periodically(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The tracking is resumed at the interval until stopped.

    Errors (e.g., of the broker) are logged, and don't end the loop.
    """
    calls: list[bool] = []

    def delay() -> None:
        """Record the call; fail on the first one."""
        calls.append(True)
        if len(calls) == 1:
            raise OperationalError("unavailable")

    monkeypatch.setattr(track_task_progress.task__resume_tracking, "delay", delay)
    stop = Stop(waits=2)
    track_task_progress.resume_tracking_periodically(
        stop=stop,  # type: ignore[arg-type]
        interval=5,
    )
    assert calls == [True, True]
    assert stop.waits == [5, 5, 5]
    assert "Could not resume the tracking" in caplog.text


def test_messages_acknowledged_late() -> None:
    """Polls are acknowledged after they ran, one message per process.

    So that the broker delivers them again if the worker is lost; retries
    (while MongoDB is unavailable) are not limited.
    """
    assert TRACK.acks_late
    assert TRACK.reject_on_worker_lost
    assert TRACK.max_retries is None
    assert celery.conf.worker_prefetch_multiplier == 1


def test_logs_missing(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
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
