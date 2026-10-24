"""Controllers for GA4GH TES API endpoints."""

import logging
from typing import Any

from connexion import request
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
def CreateTask(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Create task.

    The request is read here only: the task processing gets the task and the
    request path, not the request.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments, with the task as `body`.
    """
    task_runs = TaskRuns()
    task = kwargs.pop("body", None)
    response = task_runs.create_task(task=task, collection=request.path, **kwargs)
    return response


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
