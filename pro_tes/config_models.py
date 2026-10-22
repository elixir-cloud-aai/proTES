"""Custom app config models."""

from pydantic import BaseModel, Field, PositiveInt, RootModel, model_validator


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

    charset: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
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

    post: int | None = None
    poll: int = 2
    job: int | None = None


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

    Defaults are those of the TES specification (pages of fewer than 2048
    tasks).

    Args:
        default_page_size: Number of tasks per page if the request sets no
            page size.
        max_page_size: Maximum number of tasks per page, at most 2047;
            larger requested page sizes are reduced to it.

    Attributes:
        default_page_size: Number of tasks per page if the request sets no
            page size.
        max_page_size: Maximum number of tasks per page; larger requested
            page sizes are reduced to it.
    """

    default_page_size: PositiveInt = 256
    # the TES specification allows pages of fewer than 2048 tasks
    max_page_size: PositiveInt = Field(default=2047, le=2047)

    @model_validator(mode="after")
    def check_default_page_size(self) -> "ListTasks":
        """Check that the default page size is at most the maximum.

        Returns:
            The validated configuration.

        Raises:
            ValueError: The default page size exceeds the maximum.
        """
        if self.default_page_size > self.max_page_size:
            raise ValueError(
                f"default_page_size ({self.default_page_size}) exceeds"
                f" max_page_size ({self.max_page_size})"
            )
        return self


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

    service_list: list[str] = [
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


class Middlewares(RootModel[list[list[str]]]):
    """Middleware configuration.

    Args:
        root: A list of middleware class paths.

    Attributes:
        root: A list of middleware class paths.
    """

    root: list[list[str]] = [
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


class AccessControl(BaseModel):
    """Access control settings of the app.

    Args:
        require_admin: Whether the app refuses to start unless a group is
            mapped to the role `admin` (in
            `security.access_control.group_mapping`).

    Attributes:
        require_admin: Whether the app refuses to start unless a group is
            mapped to the role `admin` (in
            `security.access_control.group_mapping`).
    """

    require_admin: bool = True


class CustomConfig(BaseModel):
    """Custom app configuration.

    Args:
        access_control: Access control settings of the app.
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.

    Attributes:
        access_control: Access control settings of the app.
        controllers: All controller-related config.
        tes: TES service list and defaults.
        store_logs: Logging preferences.
        middlewares: Middleware class paths.
    """

    access_control: AccessControl = AccessControl()
    controllers: Controllers = Controllers()
    tes: Tes = Tes()
    storeLogs: StoreLogs = StoreLogs()
    middlewares: Middlewares = Middlewares()
