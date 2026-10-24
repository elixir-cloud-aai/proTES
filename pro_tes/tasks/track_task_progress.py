"""Celery tasks that track tasks at their TES backends.

A task is tracked by a chain of Celery tasks: each polls the backend once
and, while the task is unfinished, schedules the next poll (`countdown`), so
that no worker process is blocked for the runtime of a task. Messages are
acknowledged after the poll (`acks_late`), so that the broker delivers them
again if a worker is lost.

Each task is tracked by one chain of polls only: each poll has an
identifier, and the stored task holds the identifier of its current poll
(`tracking.poll`). Each poll passes the tracking on to the next one, which
gets a new identifier; polls whose identifier is no longer current do
nothing, which ends their chain. A poll may also take over from the poll
that scheduled it (`previous`), if that one didn't pass the tracking on
(e.g., because its worker was lost). When a worker starts, the tracking of
stored unfinished tasks is resumed with new identifiers
(:py:func:`task__resume_tracking`), which ends the chains of earlier
workers.
"""

import logging
from typing import Any
from uuid import uuid4

from celery.signals import worker_ready
from foca import get_collection
from pymongo.collection import Collection

from pro_tes.celery_worker import celery
from pro_tes.exceptions import BackendNotConfigured
from pro_tes.ga4gh.tes.backends import create_client
from pro_tes.ga4gh.tes.discovery import MIN_REFRESH_INTERVAL
from pro_tes.ga4gh.tes.models import TesState, TesTask
from pro_tes.ga4gh.tes.states import States
from pro_tes.utils.db import TASK_COLLECTION, TASK_DB, DbDocumentConnector
from pro_tes.utils.misc import get_custom_config, has_credentials, strip_auth
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)
# Retries of tasks whose backend is unknown, if backends are discovered
# (every `MIN_REFRESH_INTERVAL` seconds)
BACKEND_RETRIES = 3
# Field of stored tasks with the identifier of the poll tracking them
POLL_FIELD = "tracking.poll"


def backoff(failures: int) -> int:
    """Get the number of seconds until the next poll.

    Polls are `polling.wait` seconds apart until `polling.attempts`
    consecutive polls have failed; then the interval doubles with each
    failed poll, up to `polling.max_wait` seconds.

    Args:
        failures: Number of consecutive failed polls.

    Returns:
        Seconds until the next poll.
    """
    polling = get_custom_config().controllers.post_task.polling
    if failures < polling.attempts:
        return polling.wait
    # capped exponent, so that the interval isn't computed from huge numbers
    exponent = min(failures - polling.attempts + 1, 32)
    return max(polling.wait, min(polling.wait * 2**exponent, polling.max_wait))


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
        {"$set": {POLL_FIELD: poll}},
    )
    return result.matched_count > 0


@celery.task(
    name="tasks.track_run_progress",
    bind=True,
    ignore_result=True,
    acks_late=True,
    reject_on_worker_lost=True,
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

    The state is stored if it changed; once the task is finished, its logs
    and outputs are stored, and tracking ends. Failed polls are retried; after
    `polling.attempts` consecutive failures, the state is set to `UNKNOWN`
    (the task may still run at its backend), and polls back off (see
    :py:func:`backoff`) until the backend answers again.

    proTES authenticates at the TES service as configured for it (see
    :py:mod:`pro_tes.ga4gh.tes.backends`), with fresh credentials for each
    request. If the backend is unknown and backends are discovered in a
    registry, the task is retried (`BACKEND_RETRIES` times, after
    `MIN_REFRESH_INTERVAL` seconds each), as the worker may not have read
    the registry since another process discovered the backend; otherwise,
    or then, the state is set to `SYSTEM_ERROR`.

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
        BackendNotConfigured: The backend of the task is not configured.
        ValueError: The task finished, but has no logs.
    """
    collection = get_collection(TASK_DB, TASK_COLLECTION)
    if not _claim(
        collection=collection, worker_id=worker_id, poll=poll, previous=previous
    ):
        logger.debug("[%s] Tracked by another chain of polls", worker_id)
        return
    db_client = DbDocumentConnector(collection=collection, worker_id=worker_id)

    def schedule(failures: int) -> None:
        """Schedule the next poll, and pass the tracking on to it.

        Args:
            failures: Number of consecutive failed polls.
        """
        successor = uuid4().hex
        self.apply_async(
            kwargs={
                "worker_id": worker_id,
                "remote_host": remote_host,
                "remote_base_path": remote_base_path,
                "remote_task_id": remote_task_id,
                "poll": successor,
                "previous": poll,
                "failures": failures,
            },
            countdown=backoff(failures),
        )
        # the next poll may claim the tracking itself, if this fails
        collection.update_one(
            {"worker_id": worker_id, POLL_FIELD: poll},
            {"$set": {POLL_FIELD: successor}},
        )

    try:
        cli = create_client(host=remote_host, base_path=remote_base_path)
        response = cli.get_task(task_id=remote_task_id)
    except BackendNotConfigured as exc:
        # the worker may not know a backend that another process discovered
        # in the registry yet, and reads it at most every
        # `MIN_REFRESH_INTERVAL` seconds
        if (
            get_custom_config().tes.discovery.enabled
            and self.request.retries < BACKEND_RETRIES
        ):
            raise self.retry(exc=exc, countdown=MIN_REFRESH_INTERVAL) from exc
        db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
        raise
    except Exception as exc:
        failures += 1
        attempts = get_custom_config().controllers.post_task.polling.attempts
        logger.warning(
            "[%s] Poll %d of the task at its backend failed: %s: %s",
            worker_id,
            failures,
            type(exc).__name__,
            exc,
        )
        if failures == attempts:
            logger.error(
                "[%s] %d consecutive polls failed; the state is unknown until"
                " the backend answers again",
                worker_id,
                failures,
            )
            db_client.update_task_state(state=TesState.UNKNOWN.value)
        schedule(failures=failures)
        return

    document = db_client.get_document(projection={"_id": False, "task.state": True})
    if response.state != document.task.state:
        db_client.update_task_state(state=str(response.state))
    if response.state not in States.FINISHED:
        schedule(failures=0)
        return

    task_model_converter = TaskModelConverter(task=response)
    task_converted: TesTask = task_model_converter.convert_task()

    document = db_client.get_document()

    # updating task after task is finished
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

    # updating the database
    db_client.upsert_fields_in_root_object(
        root="task", **document.task.model_dump(mode="json", exclude_none=True)
    )


@celery.task(name="tasks.resume_tracking", ignore_result=True)
def task__resume_tracking() -> int:
    """Resume the tracking of stored unfinished tasks.

    That is, of tasks with an unfinished state and a remote identifier
    (`tes_endpoint.task_id`; tasks stored by earlier versions may not have
    one). Each task gets a new poll identifier, which ends the chains of
    polls that tracked it before (e.g., of a worker that was lost), and its
    first poll is scheduled. Run when a worker starts (cf.
    :py:func:`resume_tracking_on_start`); idempotent.

    Returns:
        Number of tasks whose tracking is resumed.
    """
    collection = get_collection(TASK_DB, TASK_COLLECTION)
    query = {
        "task.state": {"$in": States.UNFINISHED},
        "tes_endpoint.host": {"$nin": ["", None]},
        "tes_endpoint.task_id": {"$type": "string"},
    }
    resumed = 0
    for document in collection.find(query, projection=["worker_id", "tes_endpoint"]):
        poll = uuid4().hex
        collection.update_one({"_id": document["_id"]}, {"$set": {POLL_FIELD: poll}})
        endpoint = document["tes_endpoint"]
        task__track_task_progress.apply_async(
            kwargs={
                "worker_id": document["worker_id"],
                "remote_host": endpoint["host"],
                "remote_base_path": endpoint.get("base_path") or "",
                "remote_task_id": endpoint["task_id"],
                "poll": poll,
            },
        )
        resumed += 1
    logger.info("Resumed the tracking of %d unfinished tasks", resumed)
    return resumed


@worker_ready.connect
def resume_tracking_on_start(**kwargs: Any) -> None:
    """Resume the tracking of stored unfinished tasks when a worker starts.

    Tasks may have lost their tracking, e.g., if a worker was killed after
    it acknowledged a poll but before it scheduled the next one, or if the
    broker lost its messages. The tracking is resumed by a Celery task (cf.
    :py:func:`task__resume_tracking`), which runs in the app context.

    Args:
        **kwargs: Arguments of the signal; ignored.
    """
    task__resume_tracking.delay()
