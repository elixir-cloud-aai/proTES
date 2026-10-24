"""Tests of the Celery tasks tracking tasks at TES backends."""

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
from pro_tes.celery_worker import celery
from pro_tes.config_models import Discovery, Polling, TesBackend
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
    """States stored by the Celery task, in order."""
    calls: list[str] = []
    update = DbDocumentConnector.update_task_state

    def record(self: DbDocumentConnector, state: str = "UNKNOWN") -> None:
        """Record the state, and store it."""
        calls.append(state)
        update(self, state=state)

    monkeypatch.setattr(DbDocumentConnector, "update_task_state", record)
    return calls


@pytest.fixture
def polling(app: FlaskApp) -> Polling:
    """Polling configuration of the app (changed by tests)."""
    return get_custom_config().controllers.post_task.polling


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


def test_backend_not_configured(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
) -> None:
    """Tasks fail (`SYSTEM_ERROR`) if their backend is no longer configured."""
    get_custom_config().tes.service_list = []
    with pytest.raises(BackendNotConfigured):
        run()
    assert stored_states == ["SYSTEM_ERROR"]
    assert not client_class.clients
    assert not scheduled


class Retried(Exception):
    """Raised instead of retrying the Celery task."""


@pytest.mark.parametrize("retries, retried", [(0, True), (3, False)])
def test_backend_unknown_retried(
    tasks: Collection[dict[str, Any]],
    client_class: type[FakeClient],
    scheduled: list[dict[str, Any]],
    stored_states: list[str],
    monkeypatch: pytest.MonkeyPatch,
    retries: int,
    retried: bool,
) -> None:
    """With discovery, tasks of unknown backends are retried, then fail.

    The worker may not know a backend that another process discovered yet.
    """
    calls: list[dict[str, Any]] = []

    def retry(**kwargs: Any) -> Retried:
        """Record the retry."""
        calls.append(kwargs)
        return Retried()

    monkeypatch.setattr(TRACK, "retry", retry)
    monkeypatch.setattr(TRACK.request, "retries", retries, raising=False)
    tes_config = get_custom_config().tes
    tes_config.service_list = []
    tes_config.discovery = Discovery(
        registry_url="https://registry.example.org/api", auth="none"
    )
    with pytest.raises(Retried if retried else BackendNotConfigured):
        run()
    if retried:
        assert stored_states == []
        assert calls[0]["countdown"] == discovery.MIN_REFRESH_INTERVAL
        assert isinstance(calls[0]["exc"], BackendNotConfigured)
    else:
        assert stored_states == ["SYSTEM_ERROR"]
        assert not calls


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
) -> None:
    """The tracking of stored unfinished tasks with a backend is resumed.

    Each gets a new poll identifier, which ends the chains of polls that
    tracked it before (e.g., of a worker that was lost); finished tasks,
    tasks that weren't forwarded and tasks without remote identifier (of
    earlier versions) are not tracked.
    """
    endpoint = {"host": ARGS["remote_host"], "base_path": "", "task_id": "remote-1"}
    tasks.update_one({}, {"$set": {"tes_endpoint": endpoint}})
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
    }
    assert call.get("countdown") is None
    # the first poll of the earlier chain does nothing
    scheduled.clear()
    run()
    assert not scheduled
    # the resumed one tracks the task
    TRACK.run(**call["kwargs"])
    assert client_class.requested == ["remote-1"]


def test_tracking_resumed_on_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tracking is resumed when a worker starts."""
    calls: list[bool] = []
    monkeypatch.setattr(
        track_task_progress.task__resume_tracking,
        "delay",
        lambda: calls.append(True),
    )
    track_task_progress.resume_tracking_on_start(sender=None)
    assert calls == [True]


def test_messages_acknowledged_late() -> None:
    """Polls are acknowledged after they ran, one message per process.

    So that the broker delivers them again if the worker is lost.
    """
    assert TRACK.acks_late
    assert TRACK.reject_on_worker_lost
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
