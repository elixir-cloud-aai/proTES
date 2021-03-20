"""Utility functions for POST /tasks/{id}:cancel endpoint."""

import logging
from requests import HTTPError
from typing import Dict

from celery import current_app
from connexion.exceptions import Forbidden
import tes

from pro_tes.config.config_parser import get_conf
from pro_tes.errors.exceptions import TaskNotFound
from pro_tes.ga4gh.tes.states import States
from pro_tes.tasks.utils import set_task_state


# Get logger instance
logger = logging.getLogger(__name__)


# Utility function for endpoint POST /runs/<run_id>/delete
def cancel_task(
    config: Dict,
    id: str,
    *args,
    **kwargs
) -> Dict:
    """Cancels running workflow."""
    collection = get_conf(config, 'database', 'collections', 'tasks')
    document = collection.find_one(
        filter={'task_id': id},
        projection={
            'task_id_tes': True,
            'tes_uri': True,
            'task.state': True,
            'user_id': True,
            'worker_id': True,
            '_id': False,
        }
    )

    # Raise error if task was not found
    if not document:
        logger.error("Task '{id}' not found.".format(id=id))
        raise TaskNotFound

    # Raise error trying to access workflow run that is not owned by user
    # Only if authorization enabled
    if 'user_id' in kwargs and document['user_id'] != kwargs['user_id']:
        logger.error(
            (
                "User '{user_id}' is not allowed to access task '{id}'."
            ).format(
                user_id=kwargs['user_id'],
                id=id,
            )
        )
        raise Forbidden

    # If task is in cancelable state...
    if document['task']['state'] in States.CANCELABLE or \
       document['task']['state'] in States.UNDEFINED:

        # Get timeout duration
        timeout = get_conf(
            config,
            'api',
            'endpoint_params',
            'timeout_service_calls',
        )

        # Cancel local task
        current_app.control.revoke(
            document['worker_id'],
            terminate=True,
            signal='SIGKILL'
        )

        # Cancel remote task
        if document['tes_uri'] is not None and document['task_id_tes'] is not None:
            cli = tes.HTTPClient(document['tes_uri'], timeout=timeout)
            try:
                cli.cancel_task(document['task_id_tes'])
            except HTTPError:
                # TODO: handle more robustly: only 400/Bad Request is okay;
                # TODO: other errors (e.g. 500) should be dealt with
                pass

        # Write log entry
        logger.info(
            (
                "Task '{id}' (worker ID '{worker_id}') was canceled."
            ).format(
                id=id,
                worker_id=document['worker_id'],
            )
        )
        
        # Update task state 
        set_task_state(
            collection=collection,
            task_id=id,
            worker_id=document['worker_id'],
            state='CANCELED',
        )

    return {}
