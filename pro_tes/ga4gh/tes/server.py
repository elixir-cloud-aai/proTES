"""Controller for GA4GH TES API endpoints."""

import logging

from celery import current_app as celery_app
from connexion import request
from flask import current_app

import pro_tes.ga4gh.tes.endpoints.cancel_task as cancel_task
import pro_tes.ga4gh.tes.endpoints.create_task as create_task
import pro_tes.ga4gh.tes.endpoints.get_service_info as get_service_info
import pro_tes.ga4gh.tes.endpoints.get_task as get_task
import pro_tes.ga4gh.tes.endpoints.list_tasks as list_tasks
from pro_tes.security.decorators import auth_token_optional


# Get logger instance
logger = logging.getLogger(__name__)


# POST /tasks/{id}:cancel
@auth_token_optional
def CancelTask(id, *args, **kwargs):
    """Cancels unfinished task."""
    response = cancel_task.cancel_task(
        config=current_app.config,
        id=id,
        *args,
        **kwargs
    )
    log_request(request, response)
    return response


# POST /tasks
@auth_token_optional
def CreateTask(*args, **kwargs):
    """Creates task."""
    response = create_task.create_task(
        config=current_app.config,
        sender=request.environ['REMOTE_ADDR'],
        *args,
        **kwargs
    )
    log_request(request, response)
    return response


# GET /tasks/service-info
@auth_token_optional
def GetServiceInfo(*args, **kwargs):
    """Returns service info."""
    response = get_service_info.get_service_info(
        config=current_app.config,
        *args,
        **kwargs
    )
    log_request(request, response)
    return response


# GET /tasks/{id}
@auth_token_optional
def GetTask(id, *args, **kwargs):
    """Returns info for individual task."""
    response = get_task.get_task(
        config=current_app.config,
        id=id,
        *args,
        **kwargs
    )
    log_request(request, response)
    return response


# GET /tasks
@auth_token_optional
def ListTasks(*args, **kwargs):
    """Returns IDs and other info for all available tasks."""
    response = list_tasks.list_tasks(
        config=current_app.config,
        *args,
        **kwargs
    )
    log_request(request, response)
    return response


def log_request(request, response):
    """Writes request and response to log."""
    # TODO: write decorator for request logging
    logger.debug(
        (
            "Response to request \"{method} {path} {protocol}\" from "
            "{remote_addr}: {response}"
        ).format(
            method=request.environ['REQUEST_METHOD'],
            path=request.environ['PATH_INFO'],
            protocol=request.environ['SERVER_PROTOCOL'],
            remote_addr=request.environ['REMOTE_ADDR'],
            response=response,
        )
    )
