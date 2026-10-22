"""Controllers for GA4GH TES API endpoints."""

import logging
from typing import Any

from connexion import request
from foca.utils.logging import log_traffic

from pro_tes.ga4gh.tes.task_runs import TaskRuns

# pragma pylint: disable=invalid-name,unused-argument

# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@log_traffic
def CancelTask(
    id: str,  # pylint: disable=redefined-builtin
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

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    task_runs = TaskRuns()
    response = task_runs.create_task(request=request, **kwargs)
    return response


# GET /tasks/{id}
@log_traffic
def GetTask(id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:  # pylint: disable=redefined-builtin
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
    response = tasks_run.list_tasks(**kwargs)
    return response
