"""Utility function for GET /runs/{run_id} endpoint."""

from connexion.exceptions import Forbidden
import logging

from typing import Dict
from werkzeug.exceptions import BadRequest

from pro_tes.config.config_parser import get_conf
from pro_tes.errors.exceptions import TaskNotFound

# Get logger instance
logger = logging.getLogger(__name__)


# Utility function for endpoint GET /tasks/{id}
def get_task(
    config: Dict,
    id: str,
    *args,
    **kwargs
) -> Dict:
    """Gets detailed log information for specific run."""
    # Get collection
    collection_tasks = get_conf(config, 'database', 'collections', 'tasks')

    # Set filters
    if 'user_id' in kwargs:
        filter_dict = {
            'user_id': kwargs['user_id'],
            'task.id': id,
        }
    else:
        filter_dict = {
            'task.id': id,
        }

    # Set projections
    projection_MINIMAL = {
        '_id': False,
        'task.id': True,
        'task.state': True,
    }
    
    projection_BASIC = {
        '_id': False,
        'task.inputs.content': False,
        'task.logs.system_logs': False,
        'task.logs.logs.stdout': False,
        'task.logs.logs.stderr': False,
    }
    projection_FULL = {
        '_id': False,
        'task': True,
    }

    # Check view mode
    if 'view' in kwargs:
        view = kwargs['view']
    else:
        view = "BASIC"
    if view == "MINIMAL":
        projection = projection_MINIMAL
    elif view == "BASIC":
        projection = projection_BASIC
    elif view == "FULL":
        projection = projection_FULL
    else:
        raise BadRequest 
    
    # Get task
    document = collection_tasks.find_one(
        filter=filter_dict,
        projection=projection,
    )

    # Raise error if workflow run was not found or has no task ID
    if document:
        task = document['task']
    else:
        logger.error("Task '{id}' not found.".format(id=id))
        raise TaskNotFound

    # Raise error trying to access workflow run that is not owned by user
    # Only if authorization enabled
    if 'user_id' in kwargs and document['user_id'] != kwargs['user_id']:
        logger.error(
            "User '{user_id}' is not allowed to access task '{id}'.".format(
                user_id=kwargs['user_id'],
                id=id,
            )
        )
        raise Forbidden

    return task
