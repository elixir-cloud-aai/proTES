"""Tests of authentication and access control of the TES API."""

from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
import pytest
import yaml

from pro_tes.app import init_app
from tests.conftest import ADMIN, OUTSIDER, USER, make_headers, set_overlays
from tests.ga4gh.tes.conftest import BASE_PATH, TASK, FakeBackend


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


@pytest.mark.parametrize("user", [USER, ADMIN])
def test_allowed(
    client: FlaskClient, task_id: str, backend: type[FakeBackend], user: str
) -> None:
    """Users and admins may create, list, read and cancel tasks."""
    for method, path in task_operations(task_id):
        response = client.open(
            path, method=method, json=body(method, path), headers=make_headers(user)
        )
        assert response.status_code == 200, (method, path)


def test_forbidden(client: FlaskClient, task_id: str) -> None:
    """Users without a role get `403`, as problem details."""
    for method, path in task_operations(task_id):
        response = client.open(
            path, method=method, json=body(method, path), headers=make_headers(OUTSIDER)
        )
        assert response.status_code == 403, (method, path)
        assert response.get_json()["status"] == 403


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

    Here, users may no longer get or cancel tasks, but still create and list
    them.
    """
    overlay = tmp_path / "policies.yaml"
    overlay.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "access_control": {"policies": {"users-get-cancel-tasks": None}}
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
