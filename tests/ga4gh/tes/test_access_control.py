"""Tests of authentication and access control of the TES API."""

from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
import pytest

from tests.conftest import ADMIN, OUTSIDER, USER, make_headers
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
    ],
    ids=["no token", "malformed", "signature", "issuer", "audience"],
)
def test_unauthorized(
    anonymous_client: FlaskClient,
    task_id: str,
    tracked: list[dict[str, Any]],
    headers: dict[str, str],
) -> None:
    """Requests without a valid token get `401`, with a Bearer challenge.

    Only tokens of the trusted issuers, for the configured audiences, are
    valid; rejected tasks are not relayed.
    """
    tracked.clear()
    for method, path in task_operations(task_id):
        response = anonymous_client.open(
            path, method=method, json=body(method, path), headers=headers
        )
        assert response.status_code == 401, (method, path)
        assert response.get_json()["status"] == 401
        assert response.headers["WWW-Authenticate"] == (
            'Bearer error="invalid_token"' if headers else "Bearer"
        )
    assert not tracked


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
