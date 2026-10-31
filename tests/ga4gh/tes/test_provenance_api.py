"""Tests of serving the provenance of tasks (#322).

`GET /tasks/{id}/provenance` serves the provenance stored with a task, with
the plugin builds and pipelines that it refers to expanded, to the task's
owner and to admins; tasks have the gateway tags of FOCA (`gateway.*`) that
summarize it, and `forwarded_to` is deprecated.
"""

import json
from pathlib import Path
from typing import Any

from flask.testing import FlaskClient
from pymongo import MongoClient
import pytest
import tes
import yaml

import pro_tes
from pro_tes.app import init_app
from pro_tes.exceptions import BackendOutcomeUnknown
from pro_tes.ga4gh.tes import task_runs
from pro_tes.tasks.track_task_progress import _store_final
from pro_tes.utils.db import DbDocumentConnector
from tests.conftest import (
    ADMIN,
    ADMIN_USER,
    DB,
    OTHER_USER,
    OUTSIDER,
    make_headers,
    reset_gateway,
    set_overlays,
)
from tests.ga4gh.tes.conftest import (
    BACKENDS,
    BASE_PATH,
    TASK,
    FakeBackend,
    relayed,
    stored,
)

# Specification of the extensions of the TES API by proTES
ADDITIONS = Path(pro_tes.__file__).parent / "api" / "additions.openapi.yaml"


def provenance_path(task_id: str) -> str:
    """Get the path of the provenance of a task.

    Args:
        task_id: Identifier of the task.

    Returns:
        The path.
    """
    return f"{BASE_PATH}/tasks/{task_id}/provenance"


@pytest.mark.parametrize("user", [None, ADMIN, ADMIN_USER])
def test_get_provenance(
    client: FlaskClient,
    task_id: str,
    mongo_client: MongoClient[dict[str, Any]],
    user: str | None,
) -> None:
    """Owners and admins get the provenance, with its references expanded."""
    headers = make_headers(user) if user else None
    response = client.get(provenance_path(task_id), headers=headers)
    assert response.status_code == 200
    provenance = response.get_json()
    record = stored(mongo_client, task_id)["provenance"]
    assert provenance["request_id"] == record["request_id"]
    assert provenance["attempts"] == record["attempts"]
    assert (
        provenance["pipeline"]["resolved_hash"] == (record["pipeline"]["resolved_hash"])
    )
    assert provenance["pipeline"]["resolved"][0]["id"] == "route"
    assert set(provenance["builds"]) == set(provenance["plugins"])
    for key, build in provenance["builds"].items():
        assert "_id" not in build
        assert build["id"] == provenance["plugins"][key]


def test_head_provenance(client: FlaskClient, task_id: str) -> None:
    """Owners may send `HEAD` requests for the provenance."""
    assert client.head(provenance_path(task_id)).status_code == 200


@pytest.mark.parametrize("user", [OTHER_USER, OUTSIDER])
def test_get_provenance_forbidden(client: FlaskClient, task_id: str, user: str) -> None:
    """Other users than owners and admins get `403`."""
    response = client.get(provenance_path(task_id), headers=make_headers(user))
    assert response.status_code == 403


def test_get_provenance_not_found(client: FlaskClient) -> None:
    """Admins get `404` for unknown tasks, users `403`."""
    path = provenance_path("NOTASK")
    assert client.get(path, headers=make_headers(ADMIN)).status_code == 404
    assert client.get(path).status_code == 403


def test_get_provenance_none(
    client: FlaskClient, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Tasks without provenance (created by earlier versions) give `404`."""
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$unset": {"provenance": ""}}
    )
    response = client.get(provenance_path(task_id))
    assert response.status_code == 404
    assert response.get_json()["detail"] == "No provenance was recorded for the task."


def test_get_provenance_inline(
    client: FlaskClient, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Records that kept their builds and pipeline are served as they are.

    References that can't be resolved are left out.
    """
    tasks = mongo_client[DB]["tasks"]
    record = stored(mongo_client, task_id)["provenance"]
    record["pipeline"]["resolved"] = [{"id": "inline"}]
    record["builds"] = {"sha256:kept": {"id": "kept"}}
    record["plugins"]["sha256:missing"] = "missing"
    tasks.update_one({"task.id": task_id}, {"$set": {"provenance": record}})
    provenance = client.get(provenance_path(task_id)).get_json()
    assert provenance["pipeline"]["resolved"] == [{"id": "inline"}]
    assert provenance["builds"]["sha256:kept"] == {"id": "kept"}
    assert "sha256:missing" not in provenance["builds"]
    assert set(provenance["builds"]) == (
        set(record["plugins"]) - {"sha256:missing"}
    ) | {"sha256:kept"}


def test_get_provenance_no_pipeline(
    client: FlaskClient, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Records whose pipeline isn't stored (or has no hash) are served as is."""
    mongo_client[DB]["pipelines"].delete_many({})
    provenance = client.get(provenance_path(task_id)).get_json()
    assert "resolved" not in provenance["pipeline"]


def test_gateway_tags(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Tasks have FOCA's gateway tags; the relayed task has none.

    The client's tags are kept.
    """
    task = {**TASK, "tags": {"project": "x"}}
    response = client.post(f"{BASE_PATH}/tasks", json=task)
    assert response.status_code == 200
    task_id = response.get_json()["id"]
    tags = stored(mongo_client, task_id)["task"]["tags"]
    record = stored(mongo_client, task_id)["provenance"]
    assert tags["project"] == "x"
    assert tags["gateway.pipeline_source"] == "default"
    assert tags["gateway.pipeline_hash"] == record["pipeline"]["resolved_hash"]
    assert tags["gateway.plugins"].startswith("foca.routing.ordered@")
    assert tags["gateway.provenance"] == provenance_path(task_id)
    assert tags["gateway.backend"] == BACKENDS[0]
    assert tags["gateway.backend_task_id"] == "remote-1"
    assert tags["gateway.forwarded_at"] == record["attempts"][0]["started_at"]
    assert tags["gateway.attempts"] == "1"
    assert json.loads(tags["gateway.route"]) == [
        {"url": BACKENDS[0], "outcome": "accepted"}
    ]
    ((_, relayed_task),) = relayed(backend)
    assert relayed_task.tags == {"project": "x"}
    for view in ("BASIC", "FULL"):
        served = client.get(f"{BASE_PATH}/tasks/{task_id}?view={view}").get_json()
        assert served["tags"] == tags


def test_gateway_tags_failover(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """The route lists the attempts, also of backends that didn't accept."""
    backend.failing.add(BACKENDS[0])
    task_id = client.post(f"{BASE_PATH}/tasks", json=TASK).get_json()["id"]
    tags = stored(mongo_client, task_id)["task"]["tags"]
    assert tags["gateway.attempts"] == "2"
    assert tags["gateway.backend"] == BACKENDS[1]
    route = json.loads(tags["gateway.route"])
    assert [entry["outcome"] for entry in route] == ["not_accepted", "accepted"]


def test_gateway_tags_outcome_unknown(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks that a backend may have accepted have its URL, no remote ID."""

    def create_task(self: FakeBackend, task: tes.Task) -> str:
        """Fail as if the response timed out."""
        raise BackendOutcomeUnknown("timed out")

    monkeypatch.setattr(backend, "create_task", create_task)
    assert client.post(f"{BASE_PATH}/tasks", json=TASK).status_code == 502
    (document,) = mongo_client[DB]["tasks"].find()
    tags = document["task"]["tags"]
    assert tags["gateway.backend"] == BACKENDS[0]
    assert "gateway.backend_task_id" not in tags
    assert json.loads(tags["gateway.route"])[0]["outcome"] == "unknown"


def test_forwarded_to_deprecated() -> None:
    """`forwarded_to` of the task logs is deprecated, in favour of the tags."""
    spec = yaml.safe_load(ADDITIONS.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    assert schemas["tesForward"]["deprecated"] is True
    forwarded_to = schemas["tesTaskLog"]["properties"]["metadata"]["properties"][
        "forwarded_to"
    ]
    assert forwarded_to["deprecated"] is True
    operation = spec["paths"]["/tasks/{id}/provenance"]["get"]
    assert operation["operationId"] == "GetTaskProvenance"


def test_gateway_tags_tracked(
    client: FlaskClient,
    task_id: str,
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tracking of a task keeps its gateway tags when it stores the task.

    Also tags written after the tracking read the task (e.g., the routing
    tags of a task that a resumed poll found finished while it was created).
    """
    document = stored(mongo_client, task_id)
    connector = DbDocumentConnector(
        collection=mongo_client[DB]["tasks"], worker_id=document["worker_id"]
    )
    read = connector.get_document()
    tags = {**document["task"]["tags"], "gateway.attempts": "9"}
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$set": {"task.tags": tags}}
    )
    monkeypatch.setattr(connector, "get_document", lambda: read)
    response = tes.Task(
        id="remote-1",
        state="COMPLETE",
        executors=[],
        logs=[tes.TaskLog(logs=[], outputs=[])],
    )
    _store_final(
        db_client=connector, worker_id=document["worker_id"], response=response
    )
    document = stored(mongo_client, task_id)
    assert document["task"]["state"] == "COMPLETE"
    assert document["task"]["tags"] == tags


def test_admins_provenance_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    backend: type[FakeBackend],
) -> None:
    """Without the rule `admins-get-provenance`, admins get `403`."""
    overlay = tmp_path / "policies.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "access_control": {"policies": {"admins-get-provenance": None}}
                }
            }
        ),
        encoding="utf-8",
    )
    set_overlays(monkeypatch, overlay)
    reset_gateway()
    client = init_app().app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = make_headers()["Authorization"]
    task_id = client.post(f"{BASE_PATH}/tasks", json=TASK).get_json()["id"]
    path = provenance_path(task_id)
    assert client.get(path).status_code == 200
    assert client.get(path, headers=make_headers(ADMIN)).status_code == 403


def test_gateway_tags_not_built(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tasks are created if their creation tags can't be built."""

    def creation_tags(*args: Any, **kwargs: Any) -> dict[str, str]:
        """Fail to build the tags."""
        raise RuntimeError("not built")

    monkeypatch.setattr(task_runs, "creation_tags", creation_tags)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    tags = stored(mongo_client, response.get_json()["id"])["task"]["tags"]
    assert "gateway.pipeline_source" not in tags
    assert tags["gateway.backend"] == BACKENDS[0]


def test_routing_tags_not_built(
    client: FlaskClient,
    backend: type[FakeBackend],
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The attempts are stored if the routing tags can't be built."""

    def routing_tags(*args: Any, **kwargs: Any) -> dict[str, str]:
        """Fail to build the tags."""
        raise RuntimeError("not built")

    monkeypatch.setattr(task_runs, "routing_tags", routing_tags)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    document = stored(mongo_client, response.get_json()["id"])
    assert "gateway.backend" not in document["task"]["tags"]
    assert "gateway.pipeline_source" in document["task"]["tags"]
    (attempt,) = document["provenance"]["attempts"]
    assert attempt["backend"] == BACKENDS[0]
