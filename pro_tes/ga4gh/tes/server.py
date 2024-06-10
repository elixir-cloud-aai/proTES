"""Controllers for GA4GH TES API endpoints."""

import logging

from connexion import request  # type: ignore
from foca.utils.logging import log_traffic  # type: ignore

from pro_tes.ga4gh.tes.service_info import ServiceInfo
from pro_tes.ga4gh.tes.task_runs import TaskRuns

# pragma pylint: disable=invalid-name,unused-argument

# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@log_traffic
def CancelTask(
    id, *args, **kwargs  # pylint: disable=redefined-builtin
) -> dict:
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
def CreateTask(*args, **kwargs) -> dict:
    """Create task.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    task_runs = TaskRuns()
    response = task_runs.create_task(request=request, **kwargs)
    return response


# GET /tasks/service-info
@log_traffic
def GetServiceInfo(*args, **kwargs) -> dict:
    """Get service info.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    service_info = ServiceInfo()
    response = service_info.get_service_info(**kwargs)
    return response


# GET /tasks/{id}
@log_traffic
def GetTask(id, *args, **kwargs) -> dict:  # pylint: disable=redefined-builtin
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
def ListTasks(*args, **kwargs) -> dict:
    """List all available tasks.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    tasks_run = TaskRuns()
    response = tasks_run.list_tasks(**kwargs)
    return response
