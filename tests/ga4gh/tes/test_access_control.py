"""Tests of authentication and access control of the TES API."""

from pathlib import Path
from typing import Any

from connexion import FlaskApp
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from pymongo import MongoClient
import pytest
import yaml

from pro_tes.app import init_app
from pro_tes.exceptions import Unauthorized
from pro_tes.ga4gh.tes import ownership, task_runs
from tests.conftest import (
    ADMIN,
    ADMIN_USER,
    DB,
    OTHER_USER,
    OUTSIDER,
    USER,
    make_headers,
    set_overlays,
)
from tests.ga4gh.tes.conftest import BACKENDS, BASE_PATH, TASK, FakeBackend, stored


def task_operations(task_id: str) -> list[tuple[str, str]]:
    """Get the operations on tasks, with the path of a task.

    Args:
        task_id: Identifier of the task.

    Returns:
        Method and path of each operation.
    """
    return [
        ("POST", f"{BASE_PATH}/tasks"),
        ("GET", f"{BASE_PATH}/tasks"),
        ("GET", f"{BASE_PATH}/tasks/{task_id}"),
        ("POST", f"{BASE_PATH}/tasks/{task_id}:cancel"),
    ]


def body(method: str, path: str) -> dict[str, Any] | None:
    """Get the request body of an operation on tasks.

    Args:
        method: HTTP method.
        path: Request path.

    Returns:
        A task for `POST /tasks`, else none.
    """
    return TASK if (method, path) == ("POST", f"{BASE_PATH}/tasks") else None


def create_task(client: FlaskClient, user: str) -> str:
    """Create a task as a user.

    Args:
        client: Test client.
        user: User creating the task.

    Returns:
        Identifier of the task.
    """
    response = client.post(f"{BASE_PATH}/tasks", json=TASK, headers=make_headers(user))
    assert response.status_code == 200
    return str(response.get_json()["id"])


def list_ids(client: FlaskClient, user: str) -> list[str]:
    """List the identifiers of the tasks a user sees.

    Args:
        client: Test client.
        user: User listing the tasks.

    Returns:
        Identifiers of the listed tasks, newest first.
    """
    response = client.get(f"{BASE_PATH}/tasks", headers=make_headers(user))
    assert response.status_code == 200
    return [task["id"] for task in response.get_json()["tasks"]]


def test_allowed(client: FlaskClient, task_id: str, backend: type[FakeBackend]) -> None:
    """Users may create tasks, and list, read and cancel their own."""
    for method, path in task_operations(task_id):
        response = client.open(path, method=method, json=body(method, path))
        assert response.status_code == 200, (method, path)


@pytest.mark.parametrize("user", [OUTSIDER, OTHER_USER, ADMIN, ADMIN_USER])
def test_forbidden(client: FlaskClient, task_id: str, user: str) -> None:
    """Only owners may read and cancel tasks; others get `403`.

    Also admins, so that nobody but the owner sees a task's inputs and logs.
    Users without the role `user` (also admins) can't create or list tasks.
    Errors are problem details.
    """
    for method, path in task_operations(task_id):
        response = client.open(
            path, method=method, json=body(method, path), headers=make_headers(user)
        )
        if user in (OTHER_USER, ADMIN_USER) and path == f"{BASE_PATH}/tasks":
            assert response.status_code == 200, (method, path)
            continue
        assert response.status_code == 403, (method, path)
        assert response.get_json()["status"] == 403


def test_own_tasks(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Users, also admins, list only their own tasks."""
    alice = [create_task(client, USER) for _ in range(2)]
    bob = create_task(client, OTHER_USER)
    carol = create_task(client, ADMIN_USER)
    assert list_ids(client, USER) == alice[::-1]
    assert list_ids(client, OTHER_USER) == [bob]
    assert list_ids(client, ADMIN_USER) == [carol]
    response = client.get(f"{BASE_PATH}/tasks/{carol}", headers=make_headers(USER))
    assert response.status_code == 403


def test_own_tasks_pages(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Pages hold only the tasks of the user."""
    alice = [create_task(client, USER) for _ in range(3)]
    create_task(client, OTHER_USER)
    response = client.get(f"{BASE_PATH}/tasks", query_string={"page_size": 2})
    first = response.get_json()
    assert [task["id"] for task in first["tasks"]] == alice[:0:-1]
    response = client.get(
        f"{BASE_PATH}/tasks",
        query_string={"page_size": 2, "page_token": first["next_page_token"]},
    )
    last = response.get_json()
    assert [task["id"] for task in last["tasks"]] == alice[:1]
    assert last["next_page_token"] == ""


def test_owner_stored(
    client: FlaskClient, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """The owner is stored with the task, and as roles on the task.

    The roles are on the task and on its cancellation, which is not a
    sub-path of the task.
    """
    assert stored(mongo_client, task_id)["user_id"] == USER
    rules = mongo_client[DB]["policy_rules"].find({"ptype": "g2"})
    assert sorted((rule["v0"], rule["v1"], rule["v2"]) for rule in rules) == [
        (f"user:{USER}", "owner", f"{BASE_PATH}/tasks/{task_id}"),
        (f"user:{USER}", "owner", f"{BASE_PATH}/tasks/{task_id}:cancel"),
    ]


def test_owner_failed_task(client: FlaskClient, backend: type[FakeBackend]) -> None:
    """Users own tasks that could not be forwarded, too."""
    backend.failing.update(BACKENDS)
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 500
    (task_id,) = list_ids(client, USER)
    response = client.get(f"{BASE_PATH}/tasks/{task_id}")
    assert response.get_json()["state"] == "SYSTEM_ERROR"


def test_unknown_task(client: FlaskClient) -> None:
    """Unknown tasks are forbidden (`403`), as tasks of others are.

    So, responses don't tell which tasks exist.
    """
    for path in (f"{BASE_PATH}/tasks/UNKNWN", f"{BASE_PATH}/tasks/UNKNWN:cancel"):
        response = client.open(path, method="POST" if "cancel" in path else "GET")
        assert response.status_code == 403, path


@pytest.mark.parametrize("user", [USER, ADMIN_USER])
def test_tasks_without_owner(
    client: FlaskClient,
    task_id: str,
    mongo_client: MongoClient[dict[str, Any]],
    user: str,
) -> None:
    """Tasks without owner, e.g., of earlier versions, are visible to nobody.

    Also not to admins.
    """
    mongo_client[DB]["policy_rules"].delete_many({"ptype": "g2"})
    mongo_client[DB]["tasks"].update_one(
        {"task.id": task_id}, {"$unset": {"user_id": ""}}
    )
    assert list_ids(client, user) == []
    for method, path in task_operations(task_id)[2:]:
        response = client.open(path, method=method, headers=make_headers(user))
        assert response.status_code == 403, (method, path)


def test_purged_task(
    client: FlaskClient, task_id: str, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Owners get `404` for tasks deleted from the database."""
    mongo_client[DB]["tasks"].delete_one({"task.id": task_id})
    assert list_ids(client, USER) == []
    for method, path in task_operations(task_id)[2:]:
        response = client.open(path, method=method)
        assert response.status_code == 404, (method, path)
        assert response.get_json() == {
            "title": "Not found",
            "status": 404,
            "detail": "The requested task wasn't found.",
        }


def test_owner_unidentified(app: FlaskApp, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks are not created if the user can't be identified (`401`)."""
    monkeypatch.setattr(ownership, "get_current_subject", lambda: None)
    with (
        app.app.test_request_context(json=TASK),
        pytest.raises(Unauthorized, match="could not be identified"),
    ):
        task_runs.TaskRuns().create_task()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
        make_headers(
            key=rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ),
        make_headers(issuer="https://other.example.org/oidc/"),
        make_headers(audience="other-client"),
        make_headers(typ="JWT"),
    ],
    ids=["no token", "malformed", "signature", "issuer", "audience", "ID token"],
)
def test_unauthorized(
    anonymous_client: FlaskClient,
    task_id: str,
    tracked: list[dict[str, Any]],
    headers: dict[str, str],
) -> None:
    """Requests without a valid token get `401`, with a Bearer challenge.

    Only access tokens (RFC 9068; not, e.g., ID tokens) of the trusted
    issuers, for the configured audiences, are valid; rejected tasks are not
    relayed.
    """
    tracked.clear()
    for method, path in task_operations(task_id):
        response = anonymous_client.open(
            path, method=method, json=body(method, path), headers=headers
        )
        assert response.status_code == 401, (method, path)
        assert response.get_json()["status"] == 401
        challenge = response.headers["WWW-Authenticate"]
        if headers:
            assert challenge.startswith('Bearer error="invalid_token"')
        else:
            assert challenge == "Bearer"
    assert not tracked


@pytest.mark.parametrize(
    "claims, status",
    [
        ({"client_id": "portal"}, 200),
        ({"azp": "portal"}, 200),
        ({"client_id": "other-portal"}, 401),
        ({}, 401),
    ],
    ids=["listed client", "listed party", "other client", "no client"],
)
def test_clients(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    claims: dict[str, Any],
    status: int,
) -> None:
    """With `security.auth.clients`, only tokens of these clients are valid.

    The client is the `client_id` claim, or the authorized party (`azp`).
    """
    overlay = tmp_path / "clients.yaml"
    overlay.write_text(
        yaml.safe_dump({"security": {"auth": {"clients": ["portal"]}}}),
        encoding="utf-8",
    )
    set_overlays(monkeypatch, overlay)
    client = init_app().app.test_client()
    response = client.get(f"{BASE_PATH}/tasks", headers=make_headers(claims=claims))
    assert response.status_code == status


def test_named_policies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    app_env: None,
    backend: type[FakeBackend],
) -> None:
    """Overlays change single policy rules by name.

    Here, owners may no longer get or cancel tasks, but users still create
    and list them.
    """
    overlay = tmp_path / "policies.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "access_control": {"policies": {"owners-get-cancel-tasks": None}}
                }
            }
        ),
        encoding="utf-8",
    )
    set_overlays(monkeypatch, overlay)
    client = init_app().app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = make_headers()["Authorization"]
    response = client.post(f"{BASE_PATH}/tasks", json=TASK)
    assert response.status_code == 200
    assert client.get(f"{BASE_PATH}/tasks").status_code == 200
    task_id = response.get_json()["id"]
    assert client.get(f"{BASE_PATH}/tasks/{task_id}").status_code == 403


def test_public(anonymous_client: FlaskClient) -> None:
    """The service info and the Swagger UI are public."""
    assert anonymous_client.get(f"{BASE_PATH}/service-info").status_code == 200
    assert anonymous_client.get(f"{BASE_PATH}/ui/").status_code == 200


PERMISSIONS = "/admin/access-control/permissions"


@pytest.mark.parametrize("user, status", [(USER, 403), (OUTSIDER, 403), (ADMIN, 200)])
def test_permissions_api(client: FlaskClient, user: str, status: int) -> None:
    """Only admins may use FOCA's permissions API."""
    response = client.get(PERMISSIONS, headers=make_headers(user))
    assert response.status_code == status


def test_permissions_api_unauthorized(anonymous_client: FlaskClient) -> None:
    """The permissions API requires an access token."""
    assert anonymous_client.get(PERMISSIONS).status_code == 401


def test_head(client: FlaskClient, task_id: str) -> None:
    """Users may send `HEAD` requests for tasks."""
    assert client.head(f"{BASE_PATH}/tasks").status_code == 200
    assert client.head(f"{BASE_PATH}/tasks/{task_id}").status_code == 200


def test_permissions_api_keeps_configured_rules(client: FlaskClient) -> None:
    """Admins can't delete the configured rules, and keep access."""
    headers = make_headers(ADMIN)
    rules = client.get(PERMISSIONS, headers=headers).get_json()["results"]
    assert rules
    for rule in rules:
        response = client.delete(f"{PERMISSIONS}/{rule['id']}", headers=headers)
        assert response.status_code == 409
    assert client.get(PERMISSIONS, headers=headers).status_code == 200
    assert client.get(f"{BASE_PATH}/tasks").status_code == 200
