"""Tests of the plugin pipelines of new tasks, with fake plugins and backends.

The plugins are registered as entry points (FOCA's fixture
`fake_plugin_entry_points`) and allowed in the config, so that the app loads
them as installed plugins.
"""

from collections.abc import Iterator
from copy import deepcopy
import json
from pathlib import Path
import time
from typing import Any

from foca import get_custom_config
from foca.plugins import (
    Backend,
    BackendResponse,
    Context,
    InboundRequest,
    OutboundRequest,
    Plugin,
    Rejected,
)
from foca.plugins.engine import PipelineRun
from pymongo import MongoClient
import pytest
import yaml

from pro_tes.app import init_app
from pro_tes.config_models import CustomConfig
from tests.conftest import DB, USER, set_overlays
from tests.ga4gh.tes.conftest import (
    BACKENDS,
    BASE_PATH,
    TASK,
    TASK_FILES,
    FakeBackend,
    PluginClient,
    relayed,
    stored,
)

# Public URL of proTES in the tests with `Via` headers
PUBLIC_URL = "https://protes.example.org"
# Calls of the plugins, e.g., what they got
CALLS: list[Any] = []


def step(*plugins: str, step_id: str = "step") -> dict[str, Any]:
    """Get a pipeline step with plugins as alternatives, in order.

    Args:
        *plugins: Plugin identifiers.
        step_id: Identifier of the step.

    Returns:
        The step (cf. `plugins.pipeline`).
    """
    return {"id": step_id, "alternatives": [{"plugin": name} for name in plugins]}


@pytest.fixture(autouse=True)
def calls() -> Iterator[list[Any]]:
    """Calls of the fake plugins of a test."""
    CALLS.clear()
    yield CALLS
    CALLS.clear()


class Recorder(Plugin[Any, Any]):
    """Records the inbound request and the caller."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Record the request and caller."""
        CALLS.append((deepcopy(request), ctx.principal))


class Rename(Plugin[Any, Any]):
    """Renames the task."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Rename the task."""
        request.payload["name"] = "renamed"


class Fail(Plugin[Any, Any]):
    """Changes the task, then fails."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Change the task, and fail."""
        request.payload["name"] = "failed"
        raise RuntimeError("plugin failed")


class Reject(Plugin[Any, Any]):
    """Rejects tasks."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Reject the task."""
        raise Rejected("Images must be signed", status=403, title="Unsigned image")


class ForgeTag(Plugin[Any, Any]):
    """Sets a tag reserved for the gateway."""

    def on_request(self, request: InboundRequest, ctx: Context) -> None:
        """Set the tag."""
        request.payload["tags"] = {"gateway.backend": "https://evil.example.org"}


class Reverse(Plugin[Any, Any]):
    """Reverses the order of the backends."""

    def on_route(self, candidates: list[Backend], ctx: Context) -> list[Backend]:
        """Reverse the backends."""
        return list(reversed(candidates))


class AddHeader(Plugin[Any, Any]):
    """Adds a header and a tag for the backend, and chooses credentials."""

    def on_forward(self, outbound: OutboundRequest, ctx: Context) -> None:
        """Add the header and tag."""
        outbound.headers["X-Backend"] = outbound.backend.id
        outbound.payload.setdefault("tags", {})["backend"] = outbound.backend.id
        outbound.auth = "none"


class BreakFirst(Plugin[Any, Any]):
    """Makes the task invalid for the first backend."""

    def on_forward(self, outbound: OutboundRequest, ctx: Context) -> None:
        """Break the task for the first backend."""
        if outbound.backend.url == BACKENDS[0]:
            outbound.payload["executors"] = "invalid"


class CredentialsForward(Plugin[Any, Any]):
    """Adds credentials to the URL of the first input while forwarding."""

    def on_forward(self, outbound: OutboundRequest, ctx: Context) -> None:
        """Add the credentials."""
        outbound.payload["inputs"][0]["url"] = "s3://key:secret@bucket/in"


class RejectResponse(Plugin[Any, Any]):
    """Rejects responses to the client."""

    def on_response(self, response: BackendResponse, ctx: Context) -> None:
        """Reject the response."""
        raise Rejected("Not for you", status=403)


class ContentFirst(Plugin[Any, Any]):
    """Adds a large inline content for the first backend."""

    def on_forward(self, outbound: OutboundRequest, ctx: Context) -> None:
        """Add the content for the first backend."""
        if outbound.backend.url == BACKENDS[0]:
            outbound.payload["inputs"] = [
                {"content": "x" * 32, "path": "/added", "type": "FILE"}
            ]


class RejectForward(Plugin[Any, Any]):
    """Rejects tasks while forwarding."""

    def on_forward(self, outbound: OutboundRequest, ctx: Context) -> None:
        """Reject the task."""
        raise Rejected("No backend for this task", status=422)


class Annotate(Plugin[Any, Any]):
    """Adds a field and a header to the response to the client."""

    def on_response(self, response: BackendResponse, ctx: Context) -> None:
        """Annotate the response."""
        response.client_payload["backend"] = response.payload["id"]
        response.client_headers["X-Annotated"] = "yes"


def test_request_plugin(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    calls: list[Any],
) -> None:
    """Plugins get the task, without credentials of the caller, and may change it.

    The caller is identified; the changed task is stored and relayed, the
    task as received is kept as the original.
    """
    client = with_plugins(
        {"test.recorder": Recorder, "test.rename": Rename},
        pipeline=[step("test.recorder", step_id="a"), step("test.rename")],
    )
    response = client.post(
        f"{BASE_PATH}/tasks", json=TASK, headers={"Cookie": "session=secret"}
    )
    assert response.status_code == 200
    ((inbound, principal),) = calls
    assert inbound.payload == TASK
    assert inbound.path == f"{BASE_PATH}/tasks"
    assert "Authorization" not in inbound.headers
    assert "Cookie" not in inbound.headers
    assert principal.subject == USER
    ((_, task),) = relayed(backend)
    assert task.name == "renamed"
    document = stored(mongo_client, response.get_json()["id"])
    assert document["task"]["name"] == "renamed"
    assert document["task_original"]["name"] == TASK["name"]


def test_failed_alternative(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
) -> None:
    """Failed alternatives leave no trace; the next alternative runs."""
    client = with_plugins(
        {"test.fail": Fail, "test.rename": Rename},
        pipeline=[step("test.fail", "test.rename")],
    )
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    ((_, task),) = relayed(backend)
    assert task.name == "renamed"


@pytest.mark.parametrize("plugin", [Fail, ForgeTag])
def test_failed_step(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    plugin: type[Plugin[Any, Any]],
) -> None:
    """Tasks fail (`500`) if all alternatives of a step fail; not stored.

    Also plugins that change tags reserved for the gateway fail.
    """
    client = with_plugins({"test.plugin": plugin}, pipeline=[step("test.plugin")])
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    problem = response.get_json()
    assert problem["problem_type"].endswith("#plugin-step-failed")
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


def test_rejected(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks that a plugin rejects get its status, title and detail; not stored."""
    client = with_plugins(
        {"test.reject": Reject, "test.rename": Rename},
        pipeline=[step("test.reject", "test.rename")],
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 403
    problem = response.get_json()
    assert problem["title"] == "Unsigned image"
    assert problem["detail"] == "Images must be signed"
    assert problem["problem_type"].endswith("#rejected")
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


def test_route_plugin(with_plugins: PluginClient, backend: type[FakeBackend]) -> None:
    """Plugins of the `route` phase choose the order of the backends."""
    backend.failing.add(BACKENDS[1])
    client = with_plugins({"test.reverse": Reverse}, pipeline=[step("test.reverse")])
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    assert [config.url for config in backend.clients] == list(reversed(BACKENDS))
    assert [url for url, _ in relayed(backend)] == [BACKENDS[0]]


def test_forward_plugin(with_plugins: PluginClient, backend: type[FakeBackend]) -> None:
    """Plugins of the `forward` phase change the request to each backend.

    Each backend gets its own copy; requests carry a `Via` header with the
    name of proTES (the host of its public URL).
    """
    backend.failing.add(BACKENDS[0])
    client = with_plugins(
        {"test.header": AddHeader},
        pipeline=[step("test.header")],
        server={"public_url": PUBLIC_URL},
    )
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    first, second = backend.outbound
    assert first.headers["X-Backend"] == BACKENDS[0]
    assert second.headers["X-Backend"] == BACKENDS[1]
    assert second.headers["Via"] == "1.1 protes.example.org"
    assert second.auth == "none"
    ((_, task),) = relayed(backend)
    assert task.tags == {"backend": BACKENDS[1]}


def test_forward_invalid(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
) -> None:
    """Tasks that a `forward` plugin made invalid go to the next backend."""
    client = with_plugins({"test.break": BreakFirst}, pipeline=[step("test.break")])
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    assert [url for url, _ in relayed(backend)] == [BACKENDS[1]]


def test_forward_credentials(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Credentials that `forward` plugins add to URLs are removed, too."""
    client = with_plugins(
        {"test.credentials": CredentialsForward}, pipeline=[step("test.credentials")]
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK_FILES)
    assert response.status_code == 200
    ((_, task),) = relayed(backend)
    assert task.inputs[0].url == "s3://bucket/in"
    assert "secret" not in str(stored(mongo_client, response.get_json()["id"]))


def test_forward_inline_content(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
) -> None:
    """Too large inline contents that `forward` plugins add aren't sent."""
    client = with_plugins(
        {"test.content": ContentFirst}, pipeline=[step("test.content")]
    )
    get_custom_config(CustomConfig).controllers.post_task.max_inline_content = 16
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 200
    assert [url for url, _ in relayed(backend)] == [BACKENDS[1]]


def test_forward_rejected(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks that a `forward` plugin rejects are not kept."""
    client = with_plugins(
        {"test.reject": RejectForward}, pipeline=[step("test.reject")]
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 422
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


def test_response_plugin(
    with_plugins: PluginClient, backend: type[FakeBackend]
) -> None:
    """Plugins of the `response` phase change the response to the client."""
    client = with_plugins({"test.annotate": Annotate}, pipeline=[step("test.annotate")])
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    body = response.get_json()
    assert body["backend"] == "remote-1"
    assert len(body["id"]) == 6
    assert response.headers["X-Annotated"] == "yes"


def test_response_rejected(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    tracked: list[dict[str, Any]],
) -> None:
    """Tasks whose response a plugin rejects are kept: they run at a backend.

    FOCA skips steps of the `response` phase that fail or reject.
    """
    client = with_plugins(
        {"test.reject": RejectResponse}, pipeline=[step("test.reject")]
    )
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert len(relayed(backend)) == 1
    (document,) = mongo_client[DB]["tasks"].find()
    assert document["task"]["id"] == response.get_json()["id"]
    assert document["tes_endpoint"]["host"] == BACKENDS[0]
    assert len(tracked) == 1
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"})


def test_client_backends(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Clients choose backends with a tag; tags under `gateway.` aren't relayed.

    The stored task keeps the client's tags.
    """
    client = with_plugins({})
    selection = {"type": "allow", "backends": [BACKENDS[1]]}
    tags = {"gateway.backends": json.dumps(selection), "project": "x"}
    response = client.post(f"{BASE_PATH}/tasks", json={**TASK, "tags": tags})
    assert response.status_code == 200
    ((url, task),) = relayed(backend)
    assert url == BACKENDS[1]
    assert task.tags == {"project": "x"}
    stored_tags = stored(mongo_client, response.get_json()["id"])["task"]["tags"]
    assert stored_tags.items() >= tags.items()


def test_reserved_tag(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks with other tags under `gateway.` are rejected (`400`)."""
    client = with_plugins({})
    task = {**TASK, "tags": {"gateway.backend": BACKENDS[1]}}
    response = client.post(f"{BASE_PATH}/tasks", json=task)
    assert response.status_code == 400
    assert "reserved" in response.get_json()["detail"]
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


def test_loop_detected(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Requests that passed proTES before are refused (`508`)."""
    client = with_plugins({}, server={"public_url": PUBLIC_URL})
    response = client.post(
        f"{BASE_PATH}/tasks",
        json=TASK,
        headers={"Via": "1.1 other.example.org, 1.1 protes.example.org"},
    )
    assert response.status_code == 508
    assert not relayed(backend)
    assert mongo_client[DB]["tasks"].count_documents({}) == 0


def test_forward_timeout(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks that no backend accepted in time fail (`503`); not kept."""
    create = backend.create_task

    def slow(self: FakeBackend, task: Any) -> str:
        """Take longer than the time to forward the task."""
        time.sleep(0.2)
        return create(self, task)

    backend.failing.add(BACKENDS[0])
    monkeypatch.setattr(backend, "create_task", slow)
    client = with_plugins({}, defaults={"forward_timeout": 0.1})
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 503
    assert response.get_json()["problem_type"].endswith("#forward-timeout")
    assert [config.url for config in backend.clients] == [BACKENDS[0]]
    assert mongo_client[DB]["tasks"].count_documents({}) == 0
    assert mongo_client[DB]["policy_rules"].count_documents({"ptype": "g2"}) == 0


def test_no_outcome(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forwarding that ends without outcome fails (not expected of FOCA)."""
    monkeypatch.setattr(PipelineRun, "attempts", lambda self, candidates: iter([]))
    client = with_plugins({})
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 500
    assert not relayed(backend)


@pytest.mark.parametrize(
    "overlay, key",
    [
        ({"custom": {"middlewares": [["a.B"]]}}, "custom.middlewares"),
        ({"middlewares": [["a.B"]]}, "middlewares"),
    ],
)
def test_legacy_middlewares(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    overlay: dict[str, Any],
    key: str,
) -> None:
    """Configs with middlewares stop the app, with a hint to plugins."""
    path = tmp_path / "overlay.yaml"
    path.write_text(yaml.safe_dump(overlay))
    set_overlays(monkeypatch, path)
    with pytest.raises(ValueError, match="replaced by FOCA plugins") as info:
        init_app()
    assert repr(key) in str(info.value)


def test_unknown_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, app_env: None
) -> None:
    """Pipelines with plugins that are not installed stop the app."""
    path = tmp_path / "overlay.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "plugins": {
                    "available": {"org.example.missing": {}},
                    "pipeline": [step("org.example.missing")],
                }
            }
        )
    )
    set_overlays(monkeypatch, path)
    with pytest.raises(ValueError, match=r"org\.example\.missing"):
        init_app()
