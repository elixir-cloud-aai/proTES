"""Utility functions for POST /v1/tasks endpoint."""

import logging
from random import choice
from requests import post
import string  # noqa: F401
from typing import (Dict, Union)

from celery import uuid
from flask import current_app
from pymongo.errors import DuplicateKeyError
from werkzeug.exceptions import BadRequest

from pro_tes.config.config_parser import (get_conf, get_conf_type)
from pro_tes.errors.errors import (Forbidden, InternalServerError)


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
    document = _init_task_document(data=kwargs['body'])

    # Get known TES instances
    document['tes_uris'] = get_conf_type(
        config,
        'tes',
        'service_list',
        types=(list),
    )

    # TODO (LATER): Get associated workflow run
    # NOTE: get run_id, run_id_secondary and user_id from callback
    
    # Create task document and insert into database
    document = _create_task_document(
        config=config,
        document=document,
        user_id=None,         # TODO: get from WES
        run_id=None,          # TODO: get from WES
        run_id_secondary=None,   # TODO: get from WES
        init_state='UNKNOWN',
        **kwargs
    )
    
    # TODO (LATER): Put on broker queue

    # Return response
    return {'id': document['task_id']}


def _init_task_document(data: Dict) -> Dict:
    """Initializes workflow run document."""
    document: Dict = dict()
    document['request'] = data
    document['task'] = data
    document['tes'] = None
    return document


def _create_task_document(
    config: Dict,
    document: Dict,
    user_id: Union[str, None] = None,
    run_id: Union[str, None] = None,
    run_id_seconary: Union[str, None] = None,
    init_state: str = 'UNKNOWN',
    **kwargs
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
        document['worker_id'] = worker_id,
        document['user_id'] = user_id
        document['run_id'] = run_id
        document['run_id_secondary'] = run_id_seconary

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

    return document


def _create_uuid(
    charset: str = '0123456789',
    length: int = 6
) -> str:
    """Creates random run ID."""
    return ''.join(choice(charset) for __ in range(length))





# FROM HERE ON: DO ON WORKER
#
#import tes
#
#from pro_tes.tasks.tasks.poll_task_state import task__poll_task_state
#
#    authorization_required = get_conf(
#        config,
#        'security',
#        'authorization_required'
#    )
#    security_params = get_conf_type(
#        config,
#        'security',
#        'jwt',
#    )
#    
#    if authorization_required:
#        try:
#            access_token = request_access_token(
#                user_id=document['user_id'],
#                token_endpoint=endpoint_params['token_endpoint'],
#                timeout=endpoint_params['timeout_token_request'],
#            )
#            validate_token(
#                token=access_token,
#                key=security_params['public_key'],
#                identity_claim=security_params['identity_claim'],
#            )
#        except Exception as e:
#            logger.exception(
#                (
#                    'Could not get access token from token endpoint '
#                    "'{token_endpoint}'. Original error message {type}: {msg}"
#                ).format(
#                    token_endpoint=endpoint_params['token_endpoint'],
#                    type=type(e).__name__,
#                    msg=e,
#                )
#            )
#            raise Forbidden
#    else:
#        access_token = None
#
#    testribute = TEStribute_Interface()
#    remote_urls_ordered = testribute.order_endpoint_list(
#        tes_json=body,
#        endpoints=remote_urls,
#        access_token=access_token,
#        method=endpoint_params['tes_distribution_method'],
#    )
#    
#    # Send task to best TES instance
#    try:
#        remote_id, remote_url = __send_task(
#            urls=remote_urls_ordered,
#            body=body,
#            access_token=access_token,
#            timeout=endpoint_params['timeout_tes_submission'],
#        )
#    except Exception as e:
#        logger.exception('{type}: {msg}'.format(
#            default_path=default_path,
#            config_var=config_var,
#            type=type(e).__name__,
#            msg=e,
#        )
#        raise InternalServerError
#
#    # Poll TES instance for state updates
#    __initiate_state_polling(
#        task_id=remote_id,
#        run_id=document['run_id'],
#        url=remote_url,
#        interval_polling=endpoint_params['interval_polling'],
#        timeout_polling=endpoint_params['timeout_polling'],
#        max_time_polling=endpoint_params['max_time_polling'],
#    )
#    
#    # Generate universally unique ID
#    local_id = __amend_task_id(
#        remote_id=remote_id,
#        remote_url=remote_url,
#        separator=endpoint_params['id_separator'],
#        encoding=endpoint_params['id_encoding'],
#    )
#    
#    # Format and return response
#    response = {'id': local_id}
#    return response
#
#def request_access_token(
#    user_id: str,
#    token_endpoint: str,
#    timeout: int = 5
#) -> str:
#    """Get access token from token endpoint."""
#    try: 
#        response = post(
#            token_endpoint,
#            data={'user_id': user_id},
#            timeout=timeout
#        )
#    except Exception as e:
#        raise
#    if response.status_code != 200:
#        raise ConnectionError(
#            (
#                "Could not access token endpoint '{endpoint}'. Received "
#                "status code '{code}'."
#            ).format(
#                endpoint=token_endpoint,
#                code=response.status_code
#            )
#        )
#    return response.json()['access_token']
#
#def validate_token(
#    token:str,
#    key:str,
#    identity_claim:str,
#) -> None:
#
#    # Decode token
#    try:
#        token_data = decode(
#            jwt=token,
#            key=get_conf(
#                current_app.config,
#                'security',
#                'jwt',
#                'public_key'
#            ),
#            algorithms=get_conf(
#                current_app.config,
#                'security',
#                'jwt',
#                'algorithm'
#            ),
#            verify=True,
#        )
#    except Exception as e:
#        raise ValueError(
#            (
#                'Authentication token could not be decoded. Original '
#                'error message: {type}: {msg}'
#            ).format(
#                type=type(e).__name__,
#                msg=e,
#            )
#        )
#
#    # Validate claims
#    identity_claim = get_conf(
#        current_app.config,
#        'security',
#        'jwt',
#        'identity_claim'
#    )
#    validate_claims(
#        token_data=token_data,
#        required_claims=[identity_claim],
#    )
#
#def __send_task(
#    urls: List[str],
#    body: Dict,
#    timeout: int = 5
#) -> Tuple[str, str]:
#    """Send task to TES instance."""
#    task = tes.Task(body)       # TODO: implement this properly
#    for url in urls:
#        # Try to submit task to TES instance
#        try:
#            cli = tes.HTTPClient(url, timeout=timeout)
#            task_id = cli.create_task(task)
#            # TODO: fix problem with marshaling
#        # Issue warning and try next TES instance if task submission failed
#        except Exception as e:
#            logger.warning(
#                (
#                    "Task could not be submitted to TES instance '{url}'. "
#                    'Trying next TES instance in list. Original error '
#                    "message: {type}: {msg}"
#                ).format(
#                    url=url,
#                    type=type(e).__name__,
#                    msg=e,
#                )
#            )
#            continue
#        # Return task ID and URL of TES instance
#        return (task_id, url)
#    # Log error if no suitable TES instance was found
#    raise ConnectionError(
#        'Task could not be submitted to any known TES instance.'
#    )
#
#
#def __initiate_state_polling(
#    task_id: str,
#    run_id: str,
#    url: str,
#    interval_polling: int = 2,
#    timeout_polling: int = 1,
#    max_time_polling: Optional[int] = None
#) -> None:
#    """Initiate polling of TES instance for task state."""
#    celery_id = uuid()
#    logger.debug(
#        (
#            "Starting polling of TES task '{task_id}' in "
#            "background task '{celery_id}'..."
#        ).format(
#            task_id=task_id,
#            celery_id=celery_id,
#        )
#    )
#    task__poll_task_state.apply_async(
#        None,
#        {
#            'task_id': task_id,
#            'run_id': run_id,
#            'url': url,
#            'interval': interval_polling,
#            'timeout': timeout_polling,
#        },
#        task_id=celery_id,
#        soft_time_limit=max_time_polling,
#    )
#    return None
#
#
#def __amend_task_id(
#    remote_id: str,
#    remote_url: str,
#    separator: str = '@',   # TODO: add to config
#    encoding: str= 'utf-8'  # TODO: add to config
#) -> str:
#    """Appends base64 to remote task ID."""
#    append = base64.b64encode(remote_url.encode(encoding))
#    return separator.join([remote_id, append])