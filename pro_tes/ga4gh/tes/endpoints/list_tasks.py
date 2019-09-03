"""Utility function for GET /runs endpoint."""

import logging
from typing import Dict

from werkzeug.exceptions import BadRequest

from pro_tes.config.config_parser import get_conf


# Get logger instance
logger = logging.getLogger(__name__)


# Utility function for endpoint GET /runs
def list_tasks(
    config: Dict,
    *args,
    **kwargs,
) -> Dict:
    """Lists IDs and status for all workflow runs."""
    collection_tasks = get_conf(config, 'database', 'collections', 'tasks')

    # TODO: stable ordering (newest last?)
    # TODO: implement next page token

    # Fall back to default page size if not provided by user
    # TODO: uncomment when implementing pagination
    # if 'page_size' in kwargs:
    #     page_size = kwargs['page_size']
    # else:
    #     page_size = (
    #         cnx_app.app.config
    #         ['api']
    #         ['endpoint_params']
    #         ['default_page_size']
    # )

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
    
    # Query database for workflow runs
    if 'user_id' in kwargs:
        filter_dict = {'user_id': kwargs['user_id']}
    else:
        filter_dict = {}

    # Get tasks    
    cursor = collection_tasks.find(
        filter=filter_dict,
        projection=projection,
    )
    tasks_list = list()
    for record in cursor:
        tasks_list.append(record['task'])

    # Return response
    return {
        'next_page_token': 'token',
        'tasks': tasks_list
    }
