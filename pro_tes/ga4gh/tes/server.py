"""Controller for GA4GH TES API endpoints."""
import logging
from foca.utils.logging import log_traffic

from connexion import request

from typing import (
    Dict
)
from pro_tes.ga4gh.tes.service_info import ServiceInfo
from pro_tes.ga4gh.tes.task_runs import TaskRuns


# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@log_traffic
def CancelTask(id, *args, **kwargs):
    """Cancels unfinished task."""
    task_runs = TaskRuns()
    response = task_runs.cancel_task(
        id=id,
        **kwargs
    )
    return response


# POST /tasks
@log_traffic
def CreateTask(*args, **kwargs) -> Dict[str, str]:
    # """Creates task."""
    task_runs = TaskRuns()
    response = task_runs.create_task(
        request=request,
        **kwargs
    )
    return response


# GET /tasks/service-info
@log_traffic
def GetServiceInfo(*args, **kwargs):
    """Returns service info."""
    service_info = ServiceInfo()
    response = service_info.get_service_info(
        **kwargs
    )
    return response


# GET /tasks/{id}
@log_traffic
def GetTask(id, *args, **kwargs):
    """Returns info for individual task."""
    task_runs = TaskRuns()
    response = task_runs.get_task(
        id=id,
        **kwargs
    )
    return response


# GET /tasks
@log_traffic
def ListTasks(*args, **kwargs):
    """Returns IDs and other info for all available tasks."""
    tasks_run = TaskRuns()
    response = tasks_run.list_tasks(
        **kwargs
    )
    return response
