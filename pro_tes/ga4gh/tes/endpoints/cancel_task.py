"""Utility functions for POST /tasks/{id}:cancel endpoint."""

import logging
from typing import Dict

from celery import current_app
from connexion.exceptions import Forbidden
import tes

from pro_tes.config.config_parser import get_conf
from pro_tes.errors.errors import TaskNotFound
from pro_tes.ga4gh.tes.states import States


# Get logger instance
logger = logging.getLogger(__name__)


# Utility function for endpoint POST /runs/<run_id>/delete
def cancel_run(
    config: Dict,
    task_id: str,
    *args,
    **kwargs
) -> Dict:
    """Cancels running workflow."""
    collection = get_conf(config, 'database', 'collections', 'tasks')
    document = collection.find_one(
        filter={'task_id': task_id},
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
        logger.error("Task '{task_id}' not found.".format(task_id=task_id))
        raise TaskNotFound

    # Raise error trying to access workflow run that is not owned by user
    # Only if authorization enabled
    if 'user_id' in kwargs and document['user_id'] != kwargs['user_id']:
        logger.error(
            (
                "User '{user_id}' is not allowed to access task '{task_id}'."
            ).format(
                user_id=kwargs['user_id'],
                task_id=task_id,
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
            'timeout_cancel_run',
        )

        # Cancel local task
        current_app.control.revoke(document['worker_id'], terminate=True)

        # Cancel remote task
        if document['tes_uri'] is not None and document['task_id_tes'] is not None:
            cli = tes.HTTPClient(document['tes_uri'], timeout=timeout)
            cli.cancel_task(document['task_id_tes'])

    # ...else raise 404 response
    else:
        raise TaskNotFound

    return {}
