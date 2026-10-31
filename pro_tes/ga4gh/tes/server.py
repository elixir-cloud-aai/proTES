"""Controllers for GA4GH TES API endpoints."""

import logging
from typing import Any

from connexion import request
from flask.wrappers import Response
from foca.plugins import GatewayError
from foca.plugins.adapter import (
    current_principal,
    inbound_from_request,
    problem,
    request_id,
)
from foca.utils.logging import log_traffic

from pro_tes.ga4gh.tes.task_runs import TaskRuns

# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@log_traffic
def CancelTask(
    id: str,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Cancel unfinished task.

    Args:
        id: Task identifier.
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    task_runs = TaskRuns()
    response = task_runs.cancel_task(id=id, **kwargs)
    return response


# POST /tasks
@log_traffic
def CreateTask(*args: Any, **kwargs: Any) -> tuple[Any, int, dict[str, str]] | Response:
    """Create task.

    The request is read here only: the task processing gets the task, the
    request path, and the inbound request and caller as plain data (cf.
    :py:mod:`foca.plugins.adapter`), not the request. Errors of the plugin
    engine get the problem details of the error (its status, title, detail
    and problem type).

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments, with the task as `body`.

    Returns:
        Response to the client (by default, the task identifier), with its
        status and headers; or the problem details of an error of the plugin
        engine.
    """
    task_runs = TaskRuns()
    task = kwargs.pop("body", None)
    try:
        body, headers = task_runs.create_task(
            task=task,
            collection=request.path,
            inbound=inbound_from_request(task),
            principal=current_principal(),
            request_id=request_id(),
            **kwargs,
        )
    except GatewayError as exc:
        logger.warning("Task not created: %s (%s): %s", exc.title, exc.code, exc.detail)
        return problem(exc)
    return body, 200, headers


# GET /tasks/{id}
@log_traffic
def GetTask(id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Get info for individual task.

    Args:
        id: Task identifier.
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    task_runs = TaskRuns()
    response = task_runs.get_task(id=id, **kwargs)
    return response


# GET /tasks
@log_traffic
def ListTasks(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """List all available tasks.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    tasks_run = TaskRuns()
    response = tasks_run.list_tasks(collection=request.path, **kwargs)
    return response
