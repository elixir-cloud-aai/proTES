"""Function to create Celery app instance and register task monitor."""

from flask import Flask
import logging
import os

from pro_tes.factories.celery_app import create_celery_app


# Get logger instance
logger = logging.getLogger(__name__)


def register_task_service(app: Flask) -> None:
    """Instantiates Celery app and registers task monitor."""
    # Ensure that code is executed only once when app reloader is used
    if os.environ.get("WERKZEUG_RUN_MAIN") != 'true':

        # Instantiate Celery app instance
        celery_app = create_celery_app(app)

    return None
