"""Celery background task to process task asynchronously."""

import logging
from time import sleep
from typing import Dict

from foca.database.register_mongodb import _create_mongo_client
from foca.models.config import Config
from flask import Flask
from flask import current_app
import tes

from pro_tes.ga4gh.tes.models import TesState, TesTask
from pro_tes.utils.db import DbDocumentConnector
from pro_tes.ga4gh.tes.states import States
from pro_tes.celery_worker import celery
from pro_tes.utils.models import TaskModelConverter

logger = logging.getLogger(__name__)


@celery.task(
    name="tasks.track_run_progress",
    bind=True,
    ignore_result=True,
    track_started=True,
)
def task__track_task_progress(
    self,  # pylint: disable=unused-argument
    worker_id: str,
    remote_host: str,
    remote_base_path: str,
    remote_task_id: str,
) -> None:
    """Relay task run request to remote TES and track run progress.

    Args:
        remote_host: Host at which the TES API is served that is processing
            this request; note that this should include the path information
            but *not* the base path defined in the TES API specification;
            e.g., specify https://my.tes.com/api if the actual API is hosted at
            https://my.tes.com/api/ga4gh/tes/v1.
        remote_base_path: Override the default path suffix defined in the tes
            API specification, i.e., `/ga4gh/tes/v1`.
        remote_task_id: task run identifier on remote tes service.

    Returns:
        Task identifier.
    """
    foca_config: Config = current_app.config.foca
    controller_config: Dict = foca_config.controllers["post_task"]

    # create database client
    collection = _create_mongo_client(
        app=Flask(__name__),
        host=foca_config.db.host,
        port=foca_config.db.port,
        db="taskStore",
    ).db["tasks"]
    db_client = DbDocumentConnector(
        collection=collection,
        worker_id=worker_id,
    )

    # update state: INITIALIZING
    db_client.update_task_state(state=TesState.INITIALIZING.value)

    url = f"{remote_host.strip('/')}/{remote_base_path.strip('/')}"

    # fetch task log and upsert database document
    try:
        cli = tes.HTTPClient(url, timeout=5)
        response = cli.get_task(task_id=remote_task_id)
    except Exception:
        db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
        raise

    # track task progress
    task_state: TesState = TesState.UNKNOWN
    attempt: int = 1
    while task_state not in States.FINISHED:
        sleep(controller_config["polling"]["wait"])
        try:
            response = cli.get_task(
                task_id=remote_task_id,
            )
        except Exception as exc:  # pylint: disable=broad-except
            if attempt <= controller_config["polling"]["attempts"]:
                attempt += 1
                logger.warning(exc, exc_info=True)
                continue
            db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
            raise
        if response.state != task_state:
            task_state = response.state
            db_client.update_task_state(state=str(task_state))

    # final update of database after task is Finished
    task_model_converter = TaskModelConverter(task=response)
    task_converted: TesTask = task_model_converter.convert_task()
    db_client.upsert_fields_in_root_object(root="task_outgoing", **task_converted.dict())
