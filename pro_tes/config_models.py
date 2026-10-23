"""Custom app config models."""

from typing import Literal
from urllib.parse import urlsplit

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
        url_credentials: What happens to tasks with credentials in the URLs
            of their inputs or outputs (`https://user:password@host/...`):
            they are rejected (`reject`), or the credentials are removed
            (`strip`); credentials are never stored or forwarded.

    Attributes:
        db: DB insert behavior.
        task_id: Task ID generation settings.
        timeout: Timeout settings.
        polling: Polling behavior.
        url_credentials: What happens to tasks with credentials in the URLs
            of their inputs or outputs (`https://user:password@host/...`):
            they are rejected (`reject`), or the credentials are removed
            (`strip`); credentials are never stored or forwarded.
    """

    db: DB = DB()
    task_id: TaskID = TaskID()
    timeout: Timeout = Timeout()
    polling: Polling = Polling()
    url_credentials: Literal["reject", "strip"] = "reject"


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


class BasicCredentials(BaseModel):
    """Names of the environment variables with HTTP Basic credentials.

    The credentials themselves are never part of the config.

    Args:
        username_env: Name of the environment variable with the user name.
        password_env: Name of the environment variable with the password.

    Attributes:
        username_env: Name of the environment variable with the user name.
        password_env: Name of the environment variable with the password.
    """

    username_env: str = Field(min_length=1)
    password_env: str = Field(min_length=1)


class TesBackend(BaseModel):
    """TES backend that tasks may be forwarded to.

    Args:
        url: Base URL of the backend, without the API path `/ga4gh/tes/v1`;
            without credentials.
        auth: How proTES authenticates at the backend: with its own access
            token (`client_credentials`, see `Tes.client_credentials`), with
            HTTP Basic credentials from the environment (`basic`, see
            `basic`), or not at all (`none`).
        basic: Environment variables with the HTTP Basic credentials; must
            be set if, and only if, `auth` is `basic`.

    Attributes:
        url: Base URL of the backend, without the API path `/ga4gh/tes/v1`;
            without credentials.
        auth: How proTES authenticates at the backend: with its own access
            token (`client_credentials`, see `Tes.client_credentials`), with
            HTTP Basic credentials from the environment (`basic`, see
            `basic`), or not at all (`none`).
        basic: Environment variables with the HTTP Basic credentials; must
            be set if, and only if, `auth` is `basic`.
    """

    url: str
    auth: Literal["client_credentials", "basic", "none"] = "client_credentials"
    basic: BasicCredentials | None = None

    @model_validator(mode="after")
    def check_backend(self) -> "TesBackend":
        """Check the URL, and that Basic credentials are set for `basic` only.

        Returns:
            The validated configuration.

        Raises:
            ValueError: The URL has no scheme or host, or has credentials;
                or `basic` is missing for `auth: basic`, or set for another
                `auth`.
        """
        try:
            parts = urlsplit(self.url)
            has_credentials = parts.username is not None or parts.password is not None
        except ValueError as exc:
            raise ValueError(f"Invalid TES backend URL: {self.url!r}") from exc
        if not (parts.scheme and parts.hostname):
            raise ValueError(f"TES backend URL without scheme or host: {self.url!r}")
        if has_credentials:
            raise ValueError(
                "TES backend URLs must not have credentials; use `auth: basic`"
                " with credentials from the environment"
            )
        if self.auth == "basic" and self.basic is None:
            raise ValueError(f"TES backend {self.url!r}: `auth: basic` needs `basic`")
        if self.auth != "basic" and self.basic is not None:
            raise ValueError(
                f"TES backend {self.url!r}: `basic` is only used with `auth: basic`"
            )
        return self


class ClientCredentials(BaseModel):
    """Client of proTES at its identity provider (OAuth 2.0 client credentials).

    proTES gets its own access tokens with the client credentials grant (RFC
    6749, section 4.4) for the TES backends with `auth: client_credentials`.
    The client ID and secret are read from the environment variables
    `PROTES_CLIENT_ID` and `PROTES_CLIENT_SECRET`, never from the config.

    Args:
        issuer: Issuer of the identity provider; its token endpoint is taken
            from its OpenID Connect discovery document unless
            `token_endpoint` is set.
        token_endpoint: Token endpoint of the identity provider.
        scope: Scope of the requested tokens (space-separated), if any.
        auth_method: How the client authenticates at the token endpoint:
            with HTTP Basic (`client_secret_basic`) or in the request body
            (`client_secret_post`).
        refresh_before: Seconds before the expiry of a token at which a new
            one is fetched (at most half its lifetime).
        timeout: Timeout of requests to the identity provider, in seconds.

    Attributes:
        issuer: Issuer of the identity provider; its token endpoint is taken
            from its OpenID Connect discovery document unless
            `token_endpoint` is set.
        token_endpoint: Token endpoint of the identity provider.
        scope: Scope of the requested tokens (space-separated), if any.
        auth_method: How the client authenticates at the token endpoint:
            with HTTP Basic (`client_secret_basic`) or in the request body
            (`client_secret_post`).
        refresh_before: Seconds before the expiry of a token at which a new
            one is fetched (at most half its lifetime).
        timeout: Timeout of requests to the identity provider, in seconds.
    """

    issuer: str | None = None
    token_endpoint: str | None = None
    scope: str | None = None
    auth_method: Literal["client_secret_basic", "client_secret_post"] = (
        "client_secret_basic"
    )
    refresh_before: PositiveInt = 60
    timeout: PositiveInt = 10


class Tes(BaseModel):
    """TES backend configuration.

    Args:
        service_list: TES backends that tasks may be forwarded to.
        url_schemes: URL schemes allowed for the TES backends and the
            identity provider of `client_credentials`.
        client_credentials: Client of proTES at its identity provider, for
            the backends with `auth: client_credentials`.

    Attributes:
        service_list: TES backends that tasks may be forwarded to.
        url_schemes: URL schemes allowed for the TES backends and the
            identity provider of `client_credentials`.
        client_credentials: Client of proTES at its identity provider, for
            the backends with `auth: client_credentials`.
    """

    service_list: list[TesBackend] = []
    url_schemes: list[Literal["https", "http"]] = Field(default=["https"], min_length=1)
    client_credentials: ClientCredentials = ClientCredentials()

    @model_validator(mode="after")
    def check_urls(self) -> "Tes":
        """Check the URL schemes, and the identity provider if it is needed.

        Returns:
            The validated configuration.

        Raises:
            ValueError: A backend is listed twice, or a URL of a backend or of
                the identity provider has a scheme that is not allowed; or a
                backend has `auth: client_credentials`, but neither the
                issuer nor the token endpoint is set.
        """
        urls = [backend.url.rstrip("/") for backend in self.service_list]
        duplicates = sorted({url for url in urls if urls.count(url) > 1})
        if duplicates:
            raise ValueError(f"TES backends listed more than once: {duplicates}")
        idp = self.client_credentials
        for url in [*urls, idp.issuer, idp.token_endpoint]:
            if url is not None and urlsplit(url).scheme not in self.url_schemes:
                raise ValueError(
                    f"URL scheme of {url!r} not allowed (`url_schemes`:"
                    f" {self.url_schemes})"
                )
        uses_tokens = any(
            backend.auth == "client_credentials" for backend in self.service_list
        )
        if uses_tokens and idp.issuer is None and idp.token_endpoint is None:
            raise ValueError(
                "TES backends with `auth: client_credentials` need"
                " `client_credentials.issuer` or `token_endpoint`"
            )
        return self


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
