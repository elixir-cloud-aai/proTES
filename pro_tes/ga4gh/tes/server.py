"""Controllers for GA4GH TES API endpoints."""

import logging
from typing import Dict

from connexion import request
from foca.utils.logging import log_traffic

from pro_tes.ga4gh.tes.service_info import ServiceInfo
from pro_tes.ga4gh.tes.task_runs import TaskRuns
from pro_tes.middleware.middleware import TaskDistributionMiddleware

# pragma pylint: disable=invalid-name,unused-argument

# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@log_traffic
def CancelTask(
    id, *args, **kwargs  # pylint: disable=redefined-builtin
) -> Dict:
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
def CreateTask(*args, **kwargs) -> Dict:
    """Create task.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    task_distributor = TaskDistributionMiddleware()
    requests, start_time = task_distributor.modify_request(request=request)
    task_runs = TaskRuns()
    response = task_runs.create_task(
        request=requests, start_time=start_time, **kwargs
    )
    return response


# GET /tasks/service-info
@log_traffic
def GetServiceInfo(*args, **kwargs) -> Dict:
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
def GetTask(id, *args, **kwargs) -> Dict:  # pylint: disable=redefined-builtin
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
def ListTasks(*args, **kwargs) -> Dict:
    """List all available tasks.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """
    tasks_run = TaskRuns()
    response = tasks_run.list_tasks(**kwargs)
    return response
