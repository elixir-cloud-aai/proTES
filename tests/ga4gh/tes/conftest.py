"""Fixtures and helpers of the tests of the TES API, with fake TES backends."""

from collections.abc import Callable
from pathlib import Path
import socket
from typing import Any, ClassVar

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_custom_config
from foca.plugins import (
    Backend,
    BackendResponse,
    Context,
    InboundRequest,
    OutboundRequest,
    Plugin,
)
from foca.plugins.backends import BackendListConfig
from foca.plugins.pytest_plugin import RegisterPlugins
from pymongo import MongoClient
import pytest
import requests
import tes
import yaml

from pro_tes.app import init_app
from pro_tes.config_models import CustomConfig
from pro_tes.ga4gh.tes import backends, task_runs
from tests.conftest import DB, make_headers, reset_gateway, set_overlays

# Connecting of sockets, as before the tests block network access
_CONNECT = socket.socket.connect

BASE_PATH = "/ga4gh/tes/v1"
TASK: dict[str, Any] = {
    "name": "hello",
    "executors": [
        {"image": "alpine", "command": ["echo", "hello"], "ignore_error": True}
    ],
}
# Task with inputs and outputs, also an input with content instead of a URL
TASK_FILES: dict[str, Any] = {
    **TASK,
    "inputs": [
        {"url": "s3://bucket/in", "path": "/in", "type": "FILE"},
        {"content": "hello", "path": "/greeting", "type": "FILE"},
    ],
    "outputs": [{"url": "s3://bucket/out", "path": "/out", "type": "FILE"}],
}
# TES backends of the tests (`custom.tes.backends`)
BACKENDS = ["https://tes1.example.org", "https://tes2.example.org"]


class FakeBackend:
    """Fake TES backends: tasks are queued.

    Records tasks, cancellations and the backends that clients were created
    for; backends can be made invalid, failing, or not serving their tasks.

    Attributes:
        url: URL of the backend.
        backend: The backend.
    """

    # Tasks by backend URL
    tasks: ClassVar[dict[str, list[tes.Task]]] = {}
    canceled: ClassVar[list[str]] = []
    # Backends that clients were created for
    clients: ClassVar[list[Backend]] = []
    # Outbound requests of the tasks relayed, in order
    outbound: ClassVar[list[OutboundRequest]] = []
    # Backends whose URL is invalid, that reject tasks, and that don't serve
    # their tasks
    invalid: ClassVar[set[str]] = set()
    failing: ClassVar[set[str]] = set()
    no_tasks: ClassVar[set[str]] = set()

    def __init__(self, url: str, backend: Backend, **kwargs: Any) -> None:
        """Create a client of a backend; other arguments are ignored.

        Raises:
            ValueError: The URL is invalid.
        """
        if url.rstrip("/") in self.invalid:
            raise ValueError(f"Invalid URL: {url}")
        self.url = url.rstrip("/")
        self.backend = backend
        self.clients.append(backend)

    def forward(
        self, outbound: OutboundRequest, task: tes.Task, **kwargs: Any
    ) -> tuple[str, BackendResponse]:
        """Relay a task as prepared by the plugins (cf. :py:meth:`create_task`).

        Returns:
            Identifier of the task at the backend, and the response.
        """
        self.outbound.append(outbound)
        task_id = self.create_task(task)
        return task_id, BackendResponse(status=200, payload={"id": task_id})

    def create_task(self, task: tes.Task) -> str:
        """Create a task.

        Raises:
            requests.HTTPError: The backend rejects tasks.
        """
        if self.url in self.failing:
            raise requests.HTTPError("500")
        tasks = self.tasks.setdefault(self.url, [])
        tasks.append(task)
        return f"remote-{len(tasks)}"

    def get_task(self, task_id: str, **kwargs: Any) -> tes.Task:
        """Get a task: queued.

        Raises:
            requests.HTTPError: The backend doesn't serve its tasks.
        """
        if self.url in self.no_tasks:
            raise requests.HTTPError("404")
        return tes.Task(id=task_id, state="QUEUED", executors=[])

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task."""
        self.canceled.append(task_id)


@pytest.fixture
def tracked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Arguments of the Celery tasks started to track tasks (not run)."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        task_runs.task__track_task_progress,
        "apply_async",
        lambda args, kwargs: calls.append(kwargs),
    )
    return calls


@pytest.fixture
def backend(
    monkeypatch: pytest.MonkeyPatch, app: FlaskApp, tracked: list[dict[str, Any]]
) -> type[FakeBackend]:
    """Fake TES backends `BACKENDS`; tasks are not tracked by Celery.

    The backends need no authentication (`auth: none`).
    """
    for name in ("invalid", "failing", "no_tasks"):
        monkeypatch.setattr(FakeBackend, name, set())
    monkeypatch.setattr(FakeBackend, "tasks", {})
    monkeypatch.setattr(FakeBackend, "canceled", [])
    monkeypatch.setattr(FakeBackend, "clients", [])
    monkeypatch.setattr(FakeBackend, "outbound", [])
    monkeypatch.setattr(backends, "TesClient", FakeBackend)
    with app.app.app_context():
        get_custom_config(CustomConfig).tes.backends = tes_backends(*BACKENDS)
    return FakeBackend


def tes_backends(*urls: str, **settings: Any) -> BackendListConfig:
    """Get a backend list of TES backends.

    Args:
        *urls: URLs of the backends.
        **settings: Settings of each backend; by default `auth: none`.

    Returns:
        The backend list (`custom.tes.backends`).
    """
    settings = {"auth": "none", **settings}
    return BackendListConfig.model_validate(
        [{"type": "ga4gh-tes", "url": url, **settings} for url in urls]
    )


def relayed(backend: type[FakeBackend]) -> list[tuple[str, tes.Task]]:
    """Get the tasks relayed to the fake backends, with their URLs."""
    return [(url, task) for url, tasks in backend.tasks.items() for task in tasks]


@pytest.fixture
def task_id(client: FlaskClient, backend: type[FakeBackend]) -> str:
    """Identifier of a task relayed to a fake backend."""
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert len(relayed(backend)) == 1
    return str(response.get_json()["id"])


def stored(mongo_client: MongoClient[dict[str, Any]], task_id: str) -> dict[str, Any]:
    """Get the stored document of a task."""
    document = mongo_client[DB]["tasks"].find_one({"task.id": task_id})
    assert document is not None
    return document


def set_state(
    mongo_client: MongoClient[dict[str, Any]], task_id: str, state: str
) -> None:
    """Set the state of a stored task, as the Celery task does."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$set": {"task.state": state}}
    )


def make_inbound(task: dict[str, Any] | None) -> InboundRequest:
    """Get the inbound request of `POST /tasks` with a task.

    Args:
        task: Task (the body of the request).

    Returns:
        Inbound request.
    """
    return InboundRequest(
        method="POST",
        path=f"{BASE_PATH}/tasks",
        route=f"{BASE_PATH}/tasks",
        payload=task,
    )


PluginClient = Callable[..., FlaskClient]
"""Type of the function of the fixture `with_plugins`."""


@pytest.fixture
def with_plugins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_plugin_entry_points: RegisterPlugins,
    backend: type[FakeBackend],
) -> PluginClient:
    """Create an app with fake plugins and the fake backends `BACKENDS`.

    The plugins are registered as entry points and allowed in the config
    (`plugins.available`), so that FOCA loads them as installed plugins.

    Returns:
        Function that takes the plugin classes by identifier, the pipeline
        (`plugins.pipeline`; FOCA's default if not given) and other settings
        of `plugins` (and `server` settings as `server`), creates the app,
        and returns a client of it that sends the access token of `USER`.
    """

    def create(
        plugins: dict[str, type[Plugin[Any, Any]]],
        pipeline: list[dict[str, Any]] | None = None,
        server: dict[str, Any] | None = None,
        **settings: Any,
    ) -> FlaskClient:
        """Create the app, and get a client of it."""
        fake_plugin_entry_points(plugins)
        conf: dict[str, Any] = {
            "plugins": {"available": {name: {} for name in plugins}, **settings},
            "custom": {
                "tes": {
                    "backends": [
                        {"type": "ga4gh-tes", "url": url, "auth": "none"}
                        for url in BACKENDS
                    ]
                }
            },
        }
        if pipeline is not None:
            conf["plugins"]["pipeline"] = pipeline
        if server is not None:
            conf["server"] = server
        overlay = tmp_path / "plugins.yaml"
        overlay.write_text(yaml.safe_dump(conf))
        set_overlays(monkeypatch, overlay)
        reset_gateway()
        app = init_app()
        client = app.app.test_client()
        client.environ_base["HTTP_AUTHORIZATION"] = make_headers()["Authorization"]
        return client

    return create


class AddContent(Plugin[Any, Any]):
    """Plugin that adds an input with a large inline content."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Add the input."""
        request.payload["inputs"] = [
            {"content": "x" * 32, "path": "/added", "type": "FILE"}
        ]


class AddCredentials(Plugin[Any, Any]):
    """Plugin that adds credentials to the URL of the first input."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Add the credentials."""
        request.payload["inputs"][0]["url"] = "s3://key:secret@bucket/in"


@pytest.fixture
def loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow connections to local servers (the tests block network access).

    Requests go there directly, also behind proxies.
    """
    blocked = socket.socket.connect

    def connect(sock: socket.socket, address: Any) -> None:
        """Connect to local addresses only (else as blocked)."""
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            _CONNECT(sock, address)
            return
        blocked(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1")
