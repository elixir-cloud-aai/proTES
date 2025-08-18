"""Custom app config models."""

from typing import List, Optional
import string
from pydantic import BaseModel  # pylint: disable=no-name-in-module
from pro_tes.ga4gh.tes.models import TesServiceInfo


# pragma pylint: disable=too-few-public-methods


class DB(BaseModel):
    """DB config for post_task.

    Args:
        insert_attempts: Number of attempts to insert a new task in DB.

    Attributes:
        insert_attempts: Number of attempts to insert a new task in DB.
    """

    insert_attempts: int = 10


class TaskID(BaseModel):
    """Task ID config.

    Args:
        charset: Characters to use when generating task IDs.
        length: Length of the generated task ID.

    Attributes:
        charset: Characters to use when generating task IDs.
        length: Length of the generated task ID.
    """

    charset: str = string.ascii_uppercase + string.digits
    length: int = 6


class Timeout(BaseModel):
    """Timeout config.

    Args:
        post: Timeout for POST requests (None disables timeout).
        poll: Timeout for polling.
        job: Timeout for job execution (None disables timeout).

    Attributes:
        post: Timeout for POST requests (None disables timeout).
        poll: Timeout for polling.
        job: Timeout for job execution (None disables timeout).
    """

    post: Optional[int] = None
    poll: int = 2
    job: Optional[int] = None


class Polling(BaseModel):
    """Polling config.

    Args:
        wait: Wait time between polling attempts.
        attempts: Max polling attempts before failure.

    Attributes:
        wait: Wait time between polling attempts.
        attempts: Max polling attempts before failure.
    """

    wait: int = 3
    attempts: int = 100


class PostTask(BaseModel):
    """Configuration for POST /task.

    Args:
        db: DB insert behavior.
        task_id: Task ID generation settings.
        timeout: Timeout settings.
        polling: Polling behavior.

    Attributes:
        db: DB insert behavior.
        task_id: Task ID generation settings.
        timeout: Timeout settings.
        polling: Polling behavior.
    """

    db: DB = DB()
    task_id: TaskID = TaskID()
    timeout: Timeout = Timeout()
    polling: Polling = Polling()


class ListTasks(BaseModel):
    """Configuration for GET /tasks.

    Args:
        default_page_size: Default pagination size.

    Attributes:
        default_page_size: Default pagination size.
    """

    default_page_size: int = 5


class Monitor(BaseModel):
    """Celery monitor settings.

    Args:
        timeout: Timeout to wait for Celery monitoring.

    Attributes:
        timeout: Timeout to wait for Celery monitoring.
    """

    timeout: float = 0.1


class Celery(BaseModel):
    """Celery configuration.

    Args:
        monitor: Monitor settings.
        message_maxsize: Maximum allowed message size.

    Attributes:
        monitor: Monitor settings.
        message_maxsize: Maximum allowed message size.
    """

    monitor: Monitor = Monitor()
    message_maxsize: int = 16777216


class Controllers(BaseModel):
    """Controller configurations.

    Args:
        post_task: Settings for POST /task.
        list_tasks: Settings for GET /tasks.
        celery: Celery background task settings.

    Attributes:
        post_task: Settings for POST /task.
        list_tasks: Settings for GET /tasks.
        celery: Celery background task settings.
    """

    post_task: PostTask = PostTask()
    list_tasks: ListTasks = ListTasks()
    celery: Celery = Celery()


class Tes(BaseModel):
    """TES backend configuration.

    Args:
        service_list: List of available TES services.

    Attributes:
        service_list: List of available TES services.
    """

    service_list: List[str] = [
        "https://csc-tesk-noauth.rahtiapp.fi",
        "https://funnel.cloud.e-infra.cz/",
        "https://tesk-eu.hypatia-comp.athenarc.gr",
        "https://tesk-na.cloud.e-infra.cz",
        "https://vm4816.kaj.pouta.csc.fi/",
    ]


class StoreLogs(BaseModel):
    """Logging configuration.

    Args:
        execution_trace: Whether to store execution trace logs.

    Attributes:
        execution_trace: Whether to store execution trace logs.
    """

    execution_trace: bool = True


class Middlewares(BaseModel):
    """Middleware configuration.

    Args:
        __root__: A list of middleware class paths.

    Attributes:
        __root__: A list of middleware class paths.
    """

    __root__: List[List[str]] = [
        [
            (
                "pro_tes.plugins.middlewares.task_distribution.distance."
                "TaskDistributionDistance"
            ),
            (
                "pro_tes.plugins.middlewares.task_distribution.random."
                "TaskDistributionRandom"
            ),
        ]
    ]


class CustomConfig(BaseModel):
    """Custom app configuration.

    Args:
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.
        service_info: Metadata about the service.

    Attributes:
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.
        service_info: Metadata about the service.
    """

    controllers: Controllers = Controllers()
    tes: Tes = Tes()
    storeLogs: StoreLogs = StoreLogs()
    middlewares: Middlewares = Middlewares()
    service_info: TesServiceInfo
