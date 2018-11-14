"""Utility functions for POST /v1/tasks endpoint."""

import logging
from typing import (Dict, List, Optional, Tuple)

import tes

from pro_tes.config.config_parser import get_conf
from pro_tes.tasks.tasks.poll_task_state import task__poll_task_state


# Get logger instance
logger = logging.getLogger(__name__)


def run_workflow(
    config: Dict,
    body: Dict,
    sender: str,
    *args,
    **kwargs
) -> Dict:
    """Relays task to best TES instance; returns universally unique task id."""
    # Get config parameters
    remote_urls = get_conf_type(
        config,
        'tes',
        'service-list',
        types=(list)
    )
    timeout_tes_submission = get_conf(
        config,
        'api',
        'endpoint_params',
        'timeout_tes_submission',
    )
    interval_polling = get_conf(
        config,
        'api',
        'endpoint_params',
        'interval_polling',
    )
    timeout_polling = get_conf(
        config,
        'api',
        'endpoint_params',
        'timeout_polling',
    )
    max_time_polling = get_conf(
        config,
        'api',
        'endpoint_params',
        'max_time_polling',
    )
    id_separator = get_conf(
        config,
        'api',
        'endpoint_params',
        'id_separator',
    )
    id_separator = get_conf(
        config,
        'api',
        'endpoint_params',
        'id_encoding',
    )
    # Get associated workflow run ID
    # TODO: 
    # Handle authentication
    # TODO: 
    # Order TES instances by priority
    # TODO: remote_urls_ordered = 
    # Send task to best TES instance
    remote_id, remote_url = __send_task(
        urls=remote_urls_ordered,
        body=body,
        timeout=timeout_tes_submission,
    )
    # Set initial task state
    # TODO:
    # Poll TES instance for state updates
    initiate_state_polling(
        task_id=remote_id,
        url=remote_url,
        interval_polling=interval_polling,
        timeout_polling=timeout_polling,
        max_time_polling=max_time_polling,
    )
    # Generate universally unique ID
    local_id = __amend_task_id(
        remote_id=remote_id,
        remote_url=remote_url,
        separator=id_separator,
        encoding=id_encoding,
    )
    # 
    response = {'id': wuid}
    return response


def __send_task(
    urls: List[str],
    body: Dict,
    timeout: int = 5
) -> Tuple[str, str]:
    """Send task to TES instance."""
    task = tes.Task(body)       # TODO: implement this properly
    for url in urls:
        # Try to submit task to TES instance
        try:
            cli = tes.HTTPClient(url, timeout=timeout)
            task_id = cli.create_task(task)
        # Issue warning and try next TES instance if task submission failed
        except Exception as e:
            logger.warning(
                (
                    "Task could not be submitted to TES instance '{url}'. "
                    "Trying next TES instance in list. Original error "
                    "message: {type}: {msg}"
                ).format(
                    url=url,
                    type=type(e).__name__,
                    msg=e,
                )
            )
            continue
        # Return task ID and URL of TES instance
        return (task_id, url)
    # Log error if no suitable TES instance was found
    logger.error(
        'Task could not be submitted to any known TES instance.'
    ).format(
            url=url,
            type=type(e).__name__,
            msg=e,
    )


def __initiate_state_polling(
    task_id: str,
    url: str,
    interval_polling: int = 2,
    timeout_polling: int = 1,
    max_time_polling: Optional[int] = None
) -> None:
    """Poll TES instance for task state."""

    # Execute command as background task
    logger.info(
        (
            "Starting execution of run '{run_id}' as task '{task_id}' in "
            "'{tmp_dir}'..."
        ).format(
            run_id=run_id,
            task_id=task_id,
            tmp_dir=tmp_dir,
        )
    )
    task__poll_task_state.apply_async(
        None,
        {
            'command_list': command_list,
            'tmp_dir': tmp_dir,
        },
        task_id=task_id,
        soft_time_limit=timeout_duration,
    )
    return None


def __amend_task_id(
    remote_id: str,
    remote_url: str,
    separator: str = '@',   # TODO: add to config
    encoding: str= 'utf-8'  # TODO: add to config
) -> str:
    """Appends base64 to remote task ID."""
    append = base64.b64encode(remote_url.encode(encoding))
    return separator.join([remote_id, append])