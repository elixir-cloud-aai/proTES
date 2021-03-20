"""Utility functions for POST /v1/tasks endpoint."""

import logging
import string  # noqa: F401
from random import choice
from typing import (Dict)

from celery import uuid
from pymongo.errors import DuplicateKeyError
from werkzeug.exceptions import BadRequest

from pro_tes.config.config_parser import (get_conf, get_conf_type)
from pro_tes.tasks.tasks.submit_task import task__submit_task

# Get logger instance
logger = logging.getLogger(__name__)


def create_task(
    config: Dict,
    sender: str,
    *args,
    **kwargs
) -> Dict:
    """Relays task to best TES instance; returns universally unique task id."""
    # Validate input data
    if not 'body' in kwargs:
        raise BadRequest

    # TODO (MAYBE): Check service info compatibility

    # Initialize database document
    document: Dict = dict()
    document['request'] = kwargs['body']
    document['task'] = kwargs['body']
    document['tes_uri'] = None
    document['task_id_tes'] = None

    # Get known TES instances
    document['tes_uris'] = get_conf_type(
        config,
        'tes',
        'service_list',
        types=(list),
    )

    # Create task document and insert into database
    document = _create_task_document(
        config=config,
        document=document,
        sender=sender,
        init_state='UNKNOWN',
    )
    
    # Get timeout duration
    timeout = get_conf(
        config,
        'api',
        'endpoint_params',
        'timeout_task_execution',
    )
    if timeout is not None and timeout < 5:
        timeout = 5

    # Process and submit task asynchronously
    logger.info(
        (
            "Starting submission of task '{task_id}' as worker task "
            "'{worker_id}'..."
        ).format(
            task_id=document['task_id'],
            worker_id=document['worker_id'],
        )
    )
    task__submit_task.apply_async(
        None,
        {
            'request': document['request'],
            'task_id': document['task_id'],
            'worker_id': document['worker_id'],
            'sender': sender,
            'tes_uris': document['tes_uris'],
        },
        worker_id=document['worker_id'],
        soft_time_limit=timeout,
    )

    # Return response
    return {'id': document['task_id']}


def _create_task_document(
    config: Dict,
    document: Dict,
    sender: str,
    init_state: str = 'UNKNOWN',
) -> Dict:
    """
    Creates unique task identifier and inserts task document into database.
    """
    collection_tasks = get_conf(config, 'database', 'collections', 'tasks')
    id_charset = eval(get_conf(config, 'database', 'task_id', 'charset'))
    id_length = get_conf(config, 'database', 'task_id', 'length')

    # Keep on trying until a unique run id was found and inserted
    # TODO: If no more possible IDs => inf loop; fix (raise customerror; 500
    #       to user)
    while True:

        # Create unique task and Celery IDs
        task_id = _create_uuid(
            charset=id_charset,
            length=id_length,
        )
        worker_id = uuid()

        # Add task, work, user and run identifiers
        document['task_id'] = document['task']['id'] = task_id
        document['worker_id'] = worker_id
        document['sender'] = sender
        document['user_id'] = None
        document['token'] = None
        document['run_id'] = None
        document['run_id_secondary'] = None

        # Set initial state
        document['task']['state'] = init_state

        # Try to insert document into database
        try:
            collection_tasks.insert(document)

        # Try new run id if document already exists
        except DuplicateKeyError:
            continue

        # Catch other database errors
        except Exception as e:
            logger.exception(
                (
                    'Database error. Original error message {type}: '
                    "{msg}"
                ).format(
                    type=type(e).__name__,
                    msg=e,
                )
            )
            break
        
        # Exit loop
        break
    
    # Return 
    return document


def _create_uuid(
    charset: str = '0123456789',
    length: int = 6
) -> str:
    """Creates random run ID."""
    return ''.join(choice(charset) for __ in range(length))
