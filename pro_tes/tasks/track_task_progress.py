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
    self,
    worker_id: str,
    tes_instances: List[str],
    user: str,
    password: str,
) -> None:
    """Relay task run request to remote TES and track run progress.

    Args:
        worker_id: Worker identifier.
        tes_instances: List of TES instances, ranked in order of preference.
        user: User name for basic authentication.
        password: Password for basic authentication.
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

    # initialize task submission status
    num_submissions = 0
    total_instances = len(tes_instances)

    # try submitting the task to the TES instances in order of preference
    for tes_instance in tes_instances:
        # update state: QUEUED
        db_client.update_task_state(state=TesState.QUEUED.value)
        num_submissions += 1

        url = f"{tes_instance.strip('/')}/{controller_config['route']}"
        payload = {"task_id": str(db_client.document_id)}

        try:
            cli = tes.HTTPClient(
                url,
                timeout=5,
                user=user,
                password=password,
            )
            response = cli.post_task(payload)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(exc, exc_info=True)
            if num_submissions < total_instances:
                # try submitting the task to the next TES instance
                continue
            else:
                # update state: SYSTEM_ERROR
                db_client.update_task_state(state=TesState.SYSTEM_ERROR.value)
                raise
        else:
            # task submission successful
            break

    # track task progress
    task_state: TesState = TesState.UNKNOWN
    attempt: int = 1
    while task_state not in States.FINISHED:
        sleep(controller_config["polling"]["wait"])
        try:
            response = cli.get_task(
                task_id=response.id,
            )
        except Exception as exc:  # pylint: disable=broad-except
            if attempt <= controller_config["polling"]["attempts"]:
                attempt


    # updating task_incoming after task is finished
    document.task_incoming.state = task_converted.state
    for index, logs in enumerate(task_converted.logs):
        document.task_incoming.logs[index].logs = logs.logs
        document.task_incoming.logs[index].outputs = logs.outputs

    # updating the database
    db_client.upsert_fields_in_root_object(
        root="task_incoming", **document.task_incoming.dict()
    )
