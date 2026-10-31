"""Tests of the choices of clients per task (#218).

Clients choose the pipeline of a task (tag `gateway.pipeline`, a preset or
a pipeline as JSON) and its TES instances (tag `gateway.backends`); FOCA's
plugin engine validates both. `GET /plugins` and `GET /plugins/{plugin_id}`
(FOCA's plugin listing, under the TES base path) tell authenticated users
what they may choose, and the service info tells everyone where to look.
"""

import json
from typing import Any

from flask.testing import FlaskClient
from pymongo import MongoClient
import pytest

from tests.conftest import ADMIN, OUTSIDER, make_headers
from tests.ga4gh.tes.conftest import (
    BACKENDS,
    BASE_PATH,
    TASK,
    FakeBackend,
    PluginClient,
    relayed,
    stored,
)
from tests.ga4gh.tes.test_plugins import Rename, step

# Plugins of the tests, by identifier
PLUGINS = {"test.rename": Rename}
# Settings of `plugins` that let clients choose pipelines, with a preset
CHOICES: dict[str, Any] = {
    "presets": {"renamed": [step("test.rename")]},
    "client_pipelines": {"enabled": True},
}


def submit(client: FlaskClient, **tags: str) -> Any:
    """Submit `TASK` with tags.

    Args:
        client: Test client.
        **tags: Tags of the task, with `__` for `.` in the keys (e.g.,
            `gateway__pipeline`).

    Returns:
        The response.
    """
    task = {**TASK, "tags": {key.replace("__", "."): val for key, val in tags.items()}}
    return client.post(f"{BASE_PATH}/tasks", json=task)


def test_preset(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Clients choose a preset by name; it runs for their task only."""
    client = with_plugins(PLUGINS, **CHOICES)
    response = submit(client, gateway__pipeline="renamed")
    assert response.status_code == 200
    assert submit(client).status_code == 200
    (_, chosen), (_, default) = relayed(backend)
    assert chosen.name == "renamed"
    assert default.name == TASK["name"]
    assert chosen.tags is None or not any(
        key.startswith("gateway.") for key in chosen.tags
    )
    tags = stored(mongo_client, response.get_json()["id"])["task"]["tags"]
    assert tags["gateway.pipeline"] == "renamed"
    assert tags["gateway.pipeline_source"] == "preset:renamed"


def test_inline_pipeline(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Clients send a pipeline as JSON, framed by FOCA's route step."""
    client = with_plugins(PLUGINS, **CHOICES)
    pipeline = json.dumps([step("test.rename")])
    response = submit(client, gateway__pipeline=pipeline)
    assert response.status_code == 200
    ((url, task),) = relayed(backend)
    assert url == BACKENDS[0]
    assert task.name == "renamed"
    tags = stored(mongo_client, response.get_json()["id"])["task"]["tags"]
    assert tags["gateway.pipeline_source"] == "client"


@pytest.mark.parametrize(
    ("pipeline", "detail"),
    [
        ("unknown", "preset"),
        (json.dumps([step("test.unknown")]), "not available"),
        ("[", "JSON"),
    ],
)
def test_invalid_pipeline(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    pipeline: str,
    detail: str,
) -> None:
    """Unknown presets and plugins and invalid JSON are rejected (`400`)."""
    client = with_plugins(PLUGINS, **CHOICES)
    response = submit(client, gateway__pipeline=pipeline)
    assert response.status_code == 400
    assert detail.lower() in response.get_json()["detail"].lower()
    assert not relayed(backend)
    assert mongo_client["taskStore"]["tasks"].count_documents({}) == 0


def test_plugin_not_for_role(
    with_plugins: PluginClient, backend: type[FakeBackend]
) -> None:
    """Plugins only for other roles are not available to the caller (`400`)."""
    client = with_plugins(
        PLUGINS,
        available={"test.rename": {"roles": ["admin"]}},
        client_pipelines={"enabled": True},
    )
    response = submit(client, gateway__pipeline=json.dumps([step("test.rename")]))
    assert response.status_code == 400
    assert "not available" in response.get_json()["detail"]
    assert not relayed(backend)


def test_reserved_tag(with_plugins: PluginClient, backend: type[FakeBackend]) -> None:
    """Other tags under `gateway.` are rejected (`400`)."""
    client = with_plugins(PLUGINS, **CHOICES)
    assert submit(client, gateway__other="x").status_code == 400
    assert not relayed(backend)


def test_pipelines_disabled(
    with_plugins: PluginClient, backend: type[FakeBackend]
) -> None:
    """Without `client_pipelines.enabled`, the tag is rejected (`400`)."""
    client = with_plugins(PLUGINS, presets=CHOICES["presets"])
    assert submit(client, gateway__pipeline="renamed").status_code == 400
    assert not relayed(backend)


@pytest.mark.parametrize(
    ("selection", "used"),
    [
        ({"type": "order", "backends": [BACKENDS[1]]}, BACKENDS[1]),
        ({"type": "forbid", "backends": [BACKENDS[0]]}, BACKENDS[1]),
        ({"type": "allow", "backends": [BACKENDS[1]]}, BACKENDS[1]),
    ],
)
def test_backends(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    selection: dict[str, Any],
    used: str,
) -> None:
    """Clients order, restrict or exclude TES instances."""
    client = with_plugins({})
    response = submit(client, gateway__backends=json.dumps(selection))
    assert response.status_code == 200
    ((url, _),) = relayed(backend)
    assert url == used


def test_backends_unknown(
    with_plugins: PluginClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Unknown TES instances are never used, but recorded in the provenance."""
    client = with_plugins({})
    unknown = "https://unknown.example.org/"
    selection = {"type": "order", "backends": [unknown]}
    response = submit(client, gateway__backends=json.dumps(selection))
    assert response.status_code == 200
    ((url, _),) = relayed(backend)
    assert url == BACKENDS[0]
    document = stored(mongo_client, response.get_json()["id"])
    selection = document["provenance"]["selection"]
    assert [url.rstrip("/") for url in selection["discarded"]] == [unknown.rstrip("/")]


@pytest.mark.parametrize(
    "selection",
    ["[", json.dumps({"type": "first", "backends": []}), json.dumps([BACKENDS[0]])],
)
def test_backends_invalid(
    with_plugins: PluginClient, backend: type[FakeBackend], selection: str
) -> None:
    """Invalid selections of TES instances are rejected (`400`)."""
    client = with_plugins({})
    assert submit(client, gateway__backends=selection).status_code == 400
    assert not relayed(backend)


def test_backends_disabled(
    with_plugins: PluginClient, backend: type[FakeBackend]
) -> None:
    """Without `client_backends.enabled`, the tag is rejected (`400`)."""
    client = with_plugins({}, client_backends={"enabled": False})
    selection = json.dumps({"type": "allow", "backends": [BACKENDS[1]]})
    assert submit(client, gateway__backends=selection).status_code == 400
    assert not relayed(backend)


def test_list_plugins(with_plugins: PluginClient) -> None:
    """Users list the plugins and presets they may choose."""
    client = with_plugins(PLUGINS, **CHOICES)
    response = client.get(f"{BASE_PATH}/plugins")
    assert response.status_code == 200
    listing = response.get_json()
    # FOCA's built-in plugins are available, too
    assert {plugin["id"] for plugin in listing["plugins"]} >= {"test.rename"}
    assert [preset["name"] for preset in listing["presets"]] == ["renamed"]
    assert listing["client_pipelines"]["allowed_for_caller"] is True
    assert listing["tag_prefix"] == "gateway."
    response = client.get(f"{BASE_PATH}/plugins/test.rename")
    assert response.status_code == 200
    assert response.get_json()["id"] == "test.rename"
    assert client.get(f"{BASE_PATH}/plugins/test.unknown").status_code == 404


def test_list_plugins_disabled(with_plugins: PluginClient) -> None:
    """Without client pipelines, users may choose no plugins."""
    client = with_plugins(PLUGINS)
    listing = client.get(f"{BASE_PATH}/plugins").get_json()
    assert listing["plugins"] == []
    assert listing["client_pipelines"]["allowed_for_caller"] is False
    assert listing["client_backends"]["enabled"] is True


@pytest.mark.parametrize("path", ["plugins", "plugins/test.rename"])
def test_list_plugins_access(with_plugins: PluginClient, path: str) -> None:
    """Callers need an access token and the role `user`.

    Admins without the role `user` get `403`, too.
    """
    client = with_plugins(PLUGINS, **CHOICES)
    url = f"{BASE_PATH}/{path}"
    assert client.get(url, headers={"Authorization": ""}).status_code == 401
    assert client.get(url, headers=make_headers(OUTSIDER)).status_code == 403
    assert client.get(url, headers=make_headers(ADMIN)).status_code == 403


def test_service_info(with_plugins: PluginClient) -> None:
    """The service info tells where the plugins are listed, without plugins."""
    client = with_plugins(PLUGINS, **CHOICES)
    response = client.get(f"{BASE_PATH}/service-info", headers={"Authorization": ""})
    assert response.status_code == 200
    gateway = response.get_json()["gateway"]
    assert gateway["plugins_url"] == f"{BASE_PATH}/plugins"
    assert gateway["client_pipelines"] is True
    assert gateway["client_backends"] is True
    assert "test.rename" not in json.dumps(gateway)
