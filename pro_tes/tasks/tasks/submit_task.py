"""Celery background task to process task asynchronously."""

import logging
from datetime import datetime
from time import sleep
from typing import (Dict, List, Tuple)

import tes
from celery.exceptions import SoftTimeLimitExceeded
from dateutil.parser import parse as parse_time
from flask import current_app
from pymongo import collection as Collection
from werkzeug.exceptions import (BadRequest)

from pro_tes.celery_worker import celery
from pro_tes.config.config_parser import get_conf
from pro_tes.database.db_utils import upsert_fields_in_root_object
from foca.database.register_mongodb import create_mongo_client
from pro_tes.ga4gh.tes.states import States
from pro_tes.tasks.utils import set_task_state

# Get logger instance
logger = logging.getLogger(__name__)


@celery.task(
    name='tasks.submit_task',
    ignore_result=True,
    bind=True,
)
def task__submit_task(
    self,
    request: Dict,
    task_id: str,
    worker_id: str,
    sender: str,
    tes_uris: List,
) -> None:
    """Processes task and delivers it to TES instance."""
    # Get app config
    config = current_app.config

    # Get timeout for service calls
    timeout_service_calls = get_conf(
        config,
        'api',
        'endpoint_params',
        'timeout_service_calls',
    )

    # Create MongoDB client
    mongo = create_mongo_client(
        app=current_app,
        config=config,
    )
    collection = mongo.db['tasks']

    # Process task
    try:

        # TODO (LATER): Get associated workflow run & related info
        # NOTE: 
        # - Get the following from callback via sender:
        #   - user_id
        #   - token
        #   - run_id
        #   - run_id_secondary (worker ID on WES)
        user_id = None
        token = "ey23f423n4fln2flk3nf23lfn"
        run_id = "RUN123"
        run_id_secondary = "1234-23141-12341-12341"

        # Update database document
        upsert_fields_in_root_object(
            collection=collection,
            worker_id=worker_id,
            root='',
            user_id=user_id,
            token=token,
            run_id=run_id,
            run_id_secondary=run_id_secondary
        )

        # TODO (LATER): Apply middleware
        # - Token validation / renewal
        # - TEStribute
        # - Replace DRS IDs

        # TODO (PROPERLY): Send task to TES instance
        try:
            task_id_tes, tes_uri = _send_task(
                tes_uris=tes_uris,
                request=request,
                token=token,
                timeout=timeout_service_calls,
            )
            logger.info(
                (
                    "Task '{task_id}' was sent to TES '{tes_uri}' under remote "
                    "task ID '{task_id_tes}'."
                ).format(
                    task_id=task_id,
                    tes_uri=tes_uri,
                    task_id_tes=task_id_tes,
                )
            )

        # Handle submission failure
        except Exception as e:
            task_id_tes = None
            tes_uri = None
            set_task_state(
                collection=collection,
                task_id=task_id,
                worker_id=worker_id,
                state='SYSTEM_ERROR',
            )
            logger.error(
                (
                    "Task '{task_id}' could not be sent to any TES instance. "
                    "Task state was set to 'SYSTEM_ERROR'. Original error "
                    "message: '{type}: {msg}'"
                ).format(
                    task_id=task_id,
                    type=type(e).__name__,
                    msg='.'.join(e.args),
                )
            )
        
        # TODO: Update database document
        document = upsert_fields_in_root_object(
            collection=collection,
            worker_id=worker_id,
            root='',
            task_id_tes=task_id_tes,
            tes_uri=tes_uri,
        )

        # TODO: Initiate polling
        interval = get_conf(
            config,
            'api',
            'endpoint_params',
            'interval_polling',
        )
        max_missed_heartbeats = get_conf(
            config,
            'api',
            'endpoint_params',
            'max_missed_heartbeats',
        )
        if tes_uri is not None and task_id_tes is not None:
            _poll_task(
                collection=collection,
                task_id=task_id,
                worker_id=worker_id,
                tes_uri=tes_uri,
                tes_task_id=task_id_tes,
                initial_state=document['task']['state'],
                token=token,
                interval=interval,
                max_missed_heartbeats=max_missed_heartbeats,
                timeout=timeout_service_calls,
            )

        # TODO (LATER): Logging

    except SoftTimeLimitExceeded as e:
        set_task_state(
            collection=collection,
            task_id=task_id,
            worker_id=worker_id,
            state='SYSTEM_ERROR',
        )
        logger.warning(
            (
                "Processing/submission of '{task_id}' timed out. Task state "
                "was set to 'SYSTEM_ERROR'. Original error message: "
                "{type}: {msg}"
            ).format(
                task_id=task_id,
                type=type(e).__name__,
                msg=e,
            )
        )


def _send_task(
    tes_uris: List[str],
    request: Dict,
    token: str,
    timeout: float = 5
) -> Tuple[str, str]:
    """Send task to TES instance."""
    # Process/sanitize request for use with py-tes
    time_now = datetime.now().strftime("%m-%d-%Y %H:%M:%S")
    if not 'creation_time' in request:
        request['creation_time'] = parse_time(time_now)
    if 'inputs' in request:
        request['inputs'] = [
            tes.models.Input(**input) for input in request['inputs']
        ]
    if 'outputs' in request:
        request['outputs'] = [
            tes.models.Output(**output) for output in request['outputs']
        ]
    if 'resources' in request:
        request['resources'] = tes.models.Resources(**request['resources'])
    if 'executors' in request:
        request['executors'] = [
            tes.models.Executor(**executor) for executor in request['executors']
        ]
    if 'logs' in request:
        for log in request['logs']:
            log['start_time'] = time_now
            log['end_time'] = time_now
            if 'logs' in log:
                for inner_log in log['logs']:
                    inner_log['start_time'] = time_now
                    inner_log['end_time'] = time_now
                log['logs'] = [
                    tes.models.ExecutorLog(**log) for log in log['logs']
                ]
            if 'outputs' in log:
                for output in log['outputs']:
                    output['size_bytes'] = 0
                log['outputs'] = [
                    tes.models.OutputFileLog(**output) for output in log['outputs']
                ]
            if 'system_logs' in log:
                log['system_logs'] = [
                    tes.models.SystemLog(**log) for log in log['system_logs']
                ]
        request['logs'] = [
            tes.models.TaskLog(**log) for log in request['logs']
        ]

    # Create Task object
    try:
        task = tes.Task(**request)
    except Exception as e:
        logger.error(
            (
                "Task object could not be created. Original error message: "
                "{type}: {msg}"
            ).format(
                type=type(e).__name__,
                msg=e,
            )
        )
        raise BadRequest

    # Iterate over known TES URIs
    for tes_uri in tes_uris:

        # Try to submit task to TES instance
        try:
            cli = tes.HTTPClient(tes_uri, timeout=timeout)
            task_id = cli.create_task(task)

        # Issue warning and try next TES instance if task submission failed
        except Exception as e:
            logger.warning(
                (
                    "Task could not be submitted to TES instance '{tes_uri}'. "
                    'Trying next TES instance in list. Original error '
                    "message: {type}: {msg}"
                ).format(
                    tes_uri=tes_uri,
                    type=type(e).__name__,
                    msg=e,
                )
            )
            continue

        # Return task ID and URL of TES instance
        return (task_id, tes_uri)

    # Log error if no suitable TES instance was found
    raise ConnectionError(
        'Task could not be submitted to any known TES instance.'
    )


def _poll_task(
    collection: Collection,
    task_id: str,
    worker_id: str,
    tes_uri: str,
    tes_task_id: str,
    initial_state: str = 'UNKNOWN',
    token: str = None,
    interval: float = 2,
    max_missed_heartbeats: int = 100,
    timeout: float = 1.5,
) -> None:
    """Poll task state."""
    # Log message
    logger.info(
        (
            "Starting polling of TES task '{task_id}' with "
            "worker ID '{worker_id}' at TES '{tes_uri}'..."
        ).format(
            task_id=task_id,
            worker_id=worker_id,
            tes_uri=tes_uri,
        )
    )

    # Initialize states and counters
    state = previous_state = initial_state
    heartbeats_left = max_missed_heartbeats

    # Start polling
    while state in States.UNFINISHED:

        # Try to submit task to TES instance
        try:
            cli = tes.HTTPClient(tes_uri, timeout=timeout)
            response = cli.get_task(tes_task_id, view='MINIMAL')

        # Issue warning if heartbeat was missed
        except Exception as e:
            heartbeats_left -= 1
            logger.warning(
                (
                    "Missed heartbeat for task '{tes_task_id}' at TES "
                    "'{tes_uri}'. {heartbeats_left} heartbeats left. Original "
                    "error message: {type}: {msg}"
                ).format(
                    tes_task_id=tes_task_id,
                    tes_uri=tes_uri,
                    type=type(e).__name__,
                    msg=e,
                )
            )
            continue
        
        # Reset heartbeat counter
        heartbeats_left = max_missed_heartbeats

        # Update state in database if changed
        state = response.state
        if state != previous_state:
            set_task_state(
                collection=collection,
                task_id=task_id,
                worker_id=worker_id,
                state=state,
            )

        # Sleep for specified interval
        sleep(interval)
    
