"""Celery background task to process task asynchronously."""

import logging
from time import sleep
from typing import Any

from foca import get_collection

from pro_tes.celery_worker import celery
from pro_tes.ga4gh.tes.backends import create_client
from pro_tes.ga4gh.tes.models import TesState, TesTask
from pro_tes.ga4gh.tes.states import States
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.utils.misc import get_custom_config, has_credentials, strip_auth
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)


@celery.task(
    name="tasks.track_run_progress",
    bind=True,
    ignore_result=True,
    track_started=True,
)
def task__track_task_progress(
    self: Any,
    worker_id: str,
    remote_host: str,
    remote_base_path: str,
    remote_task_id: str,
) -> None:
    """Relay task run request to remote TES and track run progress.

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
    """
    polling = get_custom_config().controllers.post_task.polling

    # create database client
    db_client = DbDocumentConnector(
        collection=get_collection("taskStore", "tasks"),
        worker_id=worker_id,
    )

    # update state: INITIALIZING
    db_client.update_task_state(state=TesState.INITIALIZING.value)

    # fetch task log and upsert database document
    try:
        cli = create_client(host=remote_host, base_path=remote_base_path)
        response = cli.get_task(task_id=remote_task_id)
    except Exception:
        db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
        raise

    # track task progress
    task_state: TesState = TesState.UNKNOWN
    attempt: int = 1
    while task_state not in States.FINISHED:
        sleep(polling.wait)
        try:
            response = cli.get_task(
                task_id=remote_task_id,
            )
        except Exception as exc:
            if attempt <= polling.attempts:
                attempt += 1
                logger.warning(exc, exc_info=True)
                continue
            db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
            raise
        if response.state != task_state:
            task_state = response.state
            db_client.update_task_state(state=str(task_state))

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
