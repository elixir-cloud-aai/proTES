"""Celery tasks that track tasks at their TES backends.

A task is tracked by a chain of Celery tasks: each polls the backend once
and, while the task is unfinished, schedules the next poll (`countdown`), so
that no worker process is blocked for the runtime of a task. Messages are
acknowledged after the poll (`acks_late`), so that the broker delivers them
again if a worker is lost; polls that fail because MongoDB is unavailable
are retried, and those that fail because the broker is go back to it.

Each task is tracked by one chain of polls only: each poll has an
identifier, and the stored task holds the identifier of its current poll
(`tracking.poll`) and the time it was claimed (`tracking.polled_at`). Each
poll passes the tracking on to the next one, which gets a new identifier;
polls whose identifier is no longer current do nothing, which ends their
chain. A poll may also take over from the poll that scheduled it
(`previous`), if that one didn't pass the tracking on (e.g., because its
worker was lost). When a worker starts, and then periodically, the tracking
of stored unfinished tasks that haven't been polled for long is resumed
(:py:func:`task__resume_tracking`), e.g., of tasks whose polls the broker
lost.
"""

from datetime import UTC, datetime
import logging
from threading import Event, Thread
import time
from typing import Any
from uuid import uuid4

from bson.objectid import ObjectId
from celery.exceptions import Reject
from celery.signals import worker_ready
from foca import get_collection, get_custom_config
from kombu.exceptions import OperationalError
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from pro_tes.celery_worker import celery
from pro_tes.config_models import CustomConfig
from pro_tes.ga4gh.tes.backends import create_client
from pro_tes.ga4gh.tes.models import TesState, TesTask
from pro_tes.ga4gh.tes.states import States
from pro_tes.utils.db import TASK_COLLECTION, TASK_DB, DbDocumentConnector
from pro_tes.utils.misc import has_credentials, strip_auth
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)
# Fields of stored tasks with the identifier of the poll tracking them, and
# the time (seconds since the epoch) at which it claimed the tracking
POLL_FIELD = "tracking.poll"
POLLED_AT_FIELD = "tracking.polled_at"
# Field of stored tasks with the number of consecutive failed polls, which
# resumed polls continue from
FAILURES_FIELD = "tracking.failures"
# Tasks not polled for this many times `polling.max_wait` are resumed by
# `task__resume_tracking`, which runs every `polling.max_wait` seconds
STALE_FACTOR = 3
# Errors of MongoDB and the broker, after which polls are retried
INFRASTRUCTURE_ERRORS = (PyMongoError, OperationalError)
# Doublings of intervals between polls considered (more don't change them)
MAX_DOUBLINGS = 32


def backoff(failures: int) -> int:
    """Get the number of seconds until the next poll.

    Polls are `polling.wait` seconds apart until `polling.attempts`
    consecutive polls have failed; then the interval doubles with each
    failed poll, up to `polling.max_wait` seconds (values of `max_wait`
    below `wait` count as `wait`).

    Args:
        failures: Number of consecutive failed polls.

    Returns:
        Seconds until the next poll.
    """
    polling = get_custom_config(CustomConfig).controllers.post_task.polling
    if failures < polling.attempts:
        return polling.wait
    return interval(doublings=failures - polling.attempts + 1)


def interval(doublings: int) -> int:
    """Get `polling.wait` seconds, doubled a number of times.

    At most `polling.max_wait` seconds, and at least `polling.wait`.

    Args:
        doublings: Number of times the interval is doubled.

    Returns:
        Seconds.
    """
    polling = get_custom_config(CustomConfig).controllers.post_task.polling
    # capped, so that the interval isn't computed from huge numbers
    doubled = polling.wait * 2 ** min(doublings, MAX_DOUBLINGS)
    return max(polling.wait, min(doubled, polling.max_wait))


def _claim(
    collection: Collection[dict[str, Any]],
    worker_id: str,
    poll: str | None,
    previous: str | None,
) -> bool:
    """Claim the tracking of a task for a poll.

    Args:
        collection: Database collection of the tasks.
        worker_id: Worker identifier of the task.
        poll: Identifier of the poll.
        previous: Identifier of the poll that scheduled it, which it may take
            over from.

    Returns:
        Whether the poll tracks the task.
    """
    result = collection.update_one(
        {"worker_id": worker_id, POLL_FIELD: {"$in": [poll, previous]}},
        {"$set": {POLL_FIELD: poll, POLLED_AT_FIELD: time.time()}},
    )
    return result.matched_count > 0


def _schedule(
    collection: Collection[dict[str, Any]],
    kwargs: dict[str, Any],
    failures: int,
) -> None:
    """Schedule the next poll of a task, and pass the tracking on to it.

    The next poll is scheduled first, so that it can take over if passing
    the tracking on fails.

    Args:
        collection: Database collection of the tasks.
        kwargs: Arguments of the current poll (cf.
            :py:func:`task__track_task_progress`).
        failures: Number of consecutive failed polls.
    """
    successor = uuid4().hex
    task__track_task_progress.apply_async(
        kwargs={
            **kwargs,
            "poll": successor,
            "previous": kwargs["poll"],
            "failures": failures,
        },
        countdown=backoff(failures),
    )
    collection.update_one(
        {"worker_id": kwargs["worker_id"], POLL_FIELD: kwargs["poll"]},
        {"$set": {POLL_FIELD: successor, FAILURES_FIELD: failures}},
    )


def _store_state(
    collection: Collection[dict[str, Any]],
    worker_id: str,
    state: str,
    keep_canceled: bool = True,
) -> None:
    """Store the state of a task.

    Args:
        collection: Database collection of the tasks.
        worker_id: Worker identifier of the task.
        state: State of the task.
        keep_canceled: Whether a stored `CANCELED` is kept: backends cancel
            tasks asynchronously, and may report them as running (or be
            unreachable) for a while; also if the task is canceled while it
            is polled.
    """
    query: dict[str, Any] = {"worker_id": worker_id}
    if keep_canceled:
        query["task.state"] = {"$ne": TesState.CANCELED.value}
    result = collection.update_one(query, {"$set": {"task.state": state}})
    if result.modified_count:
        logger.info("[%s] %s", worker_id, state)


def _store_final(db_client: DbDocumentConnector, worker_id: str, response: Any) -> None:
    """Store the final state, logs and outputs of a finished task.

    Args:
        db_client: Database connector of the task.
        worker_id: Worker identifier of the task.
        response: Task as served by its backend.

    Raises:
        ValueError: The task has no logs.
    """
    task_converted: TesTask = TaskModelConverter(task=response).convert_task()
    document = db_client.get_document()
    document.task.state = task_converted.state
    if task_converted.logs is None or document.task.logs is None:
        raise ValueError(f"[{worker_id}] Task has no logs")
    for index, logs in enumerate(task_converted.logs):
        document.task.logs[index].logs = logs.logs
        # backends may log output URLs with credentials, e.g., of tasks that
        # earlier versions forwarded with them
        for output in logs.outputs or []:
            try:
                found = has_credentials(output.url)
            except ValueError:
                continue
            if found:
                output.url = strip_auth(output.url)
        document.task.logs[index].outputs = logs.outputs
    # only the fields tracked, so that fields written meanwhile (e.g., the
    # gateway tags of a task still being created) are kept
    db_client.upsert_fields_in_root_object(
        root="task",
        **document.task.model_dump(
            mode="json", exclude_none=True, include={"state", "logs"}
        ),
    )


def _poll(kwargs: dict[str, Any]) -> None:
    """Poll a task at its backend once, and schedule the next poll.

    Args:
        kwargs: Arguments of the poll (cf.
            :py:func:`task__track_task_progress`).

    Raises:
        ValueError: The task finished, but has no logs.
    """
    worker_id = kwargs["worker_id"]
    collection = get_collection(TASK_DB, TASK_COLLECTION)
    if not _claim(
        collection=collection,
        worker_id=worker_id,
        poll=kwargs["poll"],
        previous=kwargs["previous"],
    ):
        logger.debug("[%s] Tracked by another chain of polls", worker_id)
        return
    db_client = DbDocumentConnector(collection=collection, worker_id=worker_id)

    try:
        cli = create_client(
            host=kwargs["remote_host"], base_path=kwargs["remote_base_path"]
        )
        response = cli.get_task(task_id=kwargs["remote_task_id"])
    except INFRASTRUCTURE_ERRORS:
        # retried by the Celery task; not a failed poll
        raise
    except Exception as exc:
        # also if the backend is no longer configured or discovered (for the
        # time being), as the task may still run there
        failures = kwargs["failures"] + 1
        logger.warning(
            "[%s] Poll %d of the task at its backend failed: %s: %s",
            worker_id,
            failures,
            type(exc).__name__,
            exc,
        )
        attempts = get_custom_config(
            CustomConfig
        ).controllers.post_task.polling.attempts
        if failures == attempts:
            logger.error(
                "[%s] %d consecutive polls failed; the state is unknown until"
                " the backend answers again",
                worker_id,
                failures,
            )
        # also if `attempts` was lowered meanwhile
        if failures >= attempts:
            _store_state(
                collection=collection,
                worker_id=worker_id,
                state=TesState.UNKNOWN.value,
            )
        _schedule(collection=collection, kwargs=kwargs, failures=failures)
        return

    stored = db_client.get_document(projection={"_id": False, "task.state": True})
    finished = response.state in States.FINISHED
    if response.state != stored.task.state:
        _store_state(
            collection=collection,
            worker_id=worker_id,
            state=str(response.state),
            keep_canceled=not finished,
        )
    if not finished:
        _schedule(collection=collection, kwargs=kwargs, failures=0)
        return
    _store_final(db_client=db_client, worker_id=worker_id, response=response)


@celery.task(
    name="tasks.track_run_progress",
    bind=True,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def task__track_task_progress(
    self: Any,
    worker_id: str,
    remote_host: str,
    remote_base_path: str,
    remote_task_id: str,
    poll: str | None = None,
    previous: str | None = None,
    failures: int = 0,
) -> None:
    """Poll a task at its TES backend once, and schedule the next poll.

    The state is stored if it changed (a stored `CANCELED` only once the
    backend has finished the task); once the task is finished, its logs and
    outputs are stored, and tracking ends. Failed polls are retried, also if
    the backend is not (or no longer) configured or discovered; after
    `polling.attempts` consecutive failures, the state is set to `UNKNOWN`
    (the task may still run at its backend), and polls back off (see
    :py:func:`backoff`) until the backend answers again. Polls that fail
    because MongoDB is unavailable are retried, with the same arguments,
    after up to `polling.max_wait` seconds, until it is available again;
    polls that fail because the broker is unavailable go back to it.

    proTES authenticates at the TES service as configured for it (see
    :py:mod:`pro_tes.ga4gh.tes.backends`), with fresh credentials for each
    request.

    Args:
        self: Celery task instance (bound task).
        worker_id: Worker identifier.
        remote_host: Host at which the TES API is served that is processing
            this request; note that this should include the path information
            but *not* the base path defined in the TES API specification;
            e.g., specify https://my.tes.com/api if the actual API is hosted at
            https://my.tes.com/api/ga4gh/tes/v1.
        remote_base_path: Override the default path suffix defined in the TES
            API specification, i.e., `/ga4gh/tes/v1`.
        remote_task_id: task run identifier on remote TES service.
        poll: Identifier of the poll; `None` for the first poll after the
            task was created.
        previous: Identifier of the poll that scheduled this one.
        failures: Number of consecutive failed polls before this one.

    Raises:
        ValueError: The task finished, but has no logs.
    """
    kwargs = {
        "worker_id": worker_id,
        "remote_host": remote_host,
        "remote_base_path": remote_base_path,
        "remote_task_id": remote_task_id,
        "poll": poll,
        "previous": previous,
        "failures": failures,
    }
    try:
        _poll(kwargs)
    except OperationalError as exc:
        # the retry couldn't be published either: the broker delivers the
        # poll again
        logger.warning(
            "[%s] Poll failed, returned to the broker: %s: %s",
            worker_id,
            type(exc).__name__,
            exc,
        )
        raise Reject(exc, requeue=True) from exc
    except PyMongoError as exc:
        countdown = interval(doublings=self.request.retries)
        logger.warning(
            "[%s] Poll failed, retrying in %d seconds: %s: %s",
            worker_id,
            countdown,
            type(exc).__name__,
            exc,
        )
        try:
            # retried until MongoDB is available (`max_retries=None`)
            raise self.retry(exc=exc, countdown=countdown) from exc
        except Reject as reject:
            # the retry couldn't be published (Celery rejects the poll
            # without requeuing it): the broker delivers the poll again
            raise Reject(reject.args[0], requeue=True) from exc


@celery.task(name="tasks.resume_tracking", ignore_result=True, acks_late=True)
def task__resume_tracking() -> int:
    """Resume the tracking of stored unfinished tasks not polled for long.

    That is, of tasks with an unfinished state and a remote identifier
    (`tes_endpoint.task_id`; tasks stored by earlier versions may not have
    one) that no poll claimed in the last `STALE_FACTOR` times
    `polling.max_wait` seconds, or ever. Each gets a new poll, which takes
    over from the stored one, so that a chain that still tracks the task
    ends. Run when a worker starts, and then periodically (cf.
    :py:func:`resume_tracking_on_start`); idempotent. Tasks whose creation
    was cut off are set to `SYSTEM_ERROR` first (cf. :py:func:`_fail_cut_off`).

    Returns:
        Number of tasks whose tracking is resumed.
    """
    post_task = get_custom_config(CustomConfig).controllers.post_task
    polling = post_task.polling
    stale = time.time() - STALE_FACTOR * max(polling.max_wait, polling.wait)
    collection = get_collection(TASK_DB, TASK_COLLECTION)
    _fail_cut_off(collection=collection, max_create_time=post_task.max_create_time)
    query = {
        "task.state": {"$in": States.UNFINISHED},
        "tes_endpoint.host": {"$nin": ["", None]},
        "tes_endpoint.task_id": {"$type": "string"},
        "$or": [
            {POLLED_AT_FIELD: {"$exists": False}},
            {POLLED_AT_FIELD: {"$lt": stale}},
        ],
    }
    resumed = 0
    for document in collection.find(
        query, projection=["worker_id", "tes_endpoint", "tracking"]
    ):
        tracking = document.get("tracking") or {}
        current = tracking.get("poll")
        poll = uuid4().hex
        endpoint = document["tes_endpoint"]
        # scheduled first, so that the poll can take over if storing it fails
        task__track_task_progress.apply_async(
            kwargs={
                "worker_id": document["worker_id"],
                "remote_host": endpoint["host"],
                "remote_base_path": endpoint.get("base_path") or "",
                "remote_task_id": endpoint["task_id"],
                "poll": poll,
                "previous": current,
                "failures": tracking.get("failures") or 0,
            },
        )
        collection.update_one(
            {"_id": document["_id"], POLL_FIELD: current},
            {"$set": {POLL_FIELD: poll}},
        )
        resumed += 1
    logger.info("Resumed the tracking of %d unfinished tasks", resumed)
    return resumed


def _fail_cut_off(collection: Collection[dict[str, Any]], max_create_time: int) -> int:
    """Set tasks whose creation was cut off to `SYSTEM_ERROR`.

    That is, tasks still in state `UNKNOWN` without identifier at a TES
    instance (also none in their logs, as stored by earlier versions) that
    were stored more than `max_create_time` seconds ago (by the time of
    their document identifier, as set by MongoDB for all versions), e.g., if
    proTES stopped while forwarding them. They can be neither tracked nor
    canceled; as their TES instance may run them, they are kept, as are
    tasks that a TES instance did not confirm (cf.
    :py:class:`~pro_tes.exceptions.BackendOutcomeUnknown`).

    Args:
        collection: Database collection of the tasks.
        max_create_time: Seconds after which tasks still being created are
            set to `SYSTEM_ERROR`.

    Returns:
        Number of tasks set to `SYSTEM_ERROR`.
    """
    cutoff = datetime.fromtimestamp(time.time() - max_create_time, UTC)
    # also checked when updated (unless forwarded since); tasks stored by
    # earlier versions may have their remote identifier only in their logs:
    # in their forwards (also nested), or as `remote_task_id` (cf.
    # `pro_tes.ga4gh.tes.task_runs.TaskRuns._remote_task_id`)
    unconfirmed = {
        "task.state": TesState.UNKNOWN.value,
        "tes_endpoint.task_id": {"$not": {"$type": "string"}},
        "$nor": [
            {"task.logs.0.metadata.forwarded_to.id": {"$type": "string"}},
            {"task.logs.0.metadata.remote_task_id": {"$type": "string"}},
        ],
    }
    failed = 0
    for document in collection.find(
        {**unconfirmed, "_id": {"$lt": ObjectId.from_datetime(cutoff)}},
        projection=["worker_id"],
    ):
        result = collection.update_one(
            {"_id": document["_id"], **unconfirmed},
            {"$set": {"task.state": TesState.SYSTEM_ERROR.value}},
        )
        if result.modified_count:
            logger.warning(
                "[%s] %s: its creation was cut off (not forwarded within %d"
                " seconds); it may run at a TES instance",
                document["worker_id"],
                TesState.SYSTEM_ERROR.value,
                max_create_time,
            )
            failed += 1
    return failed


def resume_tracking_periodically(stop: Event, interval: float) -> None:
    """Resume the tracking of stale tasks every `interval` seconds.

    Args:
        stop: Event that ends the loop.
        interval: Seconds between runs of :py:func:`task__resume_tracking`.
    """
    while not stop.wait(interval):
        try:
            task__resume_tracking.delay()
        except Exception:
            logger.exception("Could not resume the tracking of tasks")


@worker_ready.connect
def resume_tracking_on_start(**kwargs: Any) -> Thread:
    """Resume the tracking of stale tasks when a worker starts, and periodically.

    Tasks may have lost their tracking, e.g., if the broker lost its
    messages. The tracking is resumed by a Celery task (cf.
    :py:func:`task__resume_tracking`), which runs in the app context, when
    the worker starts and then every `polling.max_wait` seconds, from a
    daemon thread of the worker's main process.

    Args:
        **kwargs: Arguments of the signal; ignored.

    Returns:
        Thread that resumes the tracking periodically.
    """
    # signal handlers run outside of the app context (`get_custom_config`)
    custom = (celery.conf.foca.model_extra or {}).get("custom")
    config = custom if isinstance(custom, CustomConfig) else CustomConfig()
    interval = config.controllers.post_task.polling.max_wait
    task__resume_tracking.delay()
    thread = Thread(
        target=resume_tracking_periodically,
        kwargs={"stop": Event(), "interval": interval},
        name="resume-tracking",
        daemon=True,
    )
    thread.start()
    return thread
