"""Tests of the app created from `config.yaml`, with FOCA 0.15."""

# pylint: disable=redefined-outer-name

from importlib.metadata import version

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_config
import mongomock

BASE_PATH = "/ga4gh/tes/v1"


def test_jsonschema_4() -> None:
    """The app is created (see fixtures) with jsonschema 4 (cf. #173)."""
    assert int(version("jsonschema").split(".", maxsplit=1)[0]) >= 4


def test_config(app: FlaskApp) -> None:
    """Debug mode is off, request bodies are limited, no issuer is trusted."""
    with app.app.app_context():
        conf = get_config()
    assert conf.server.debug is False
    assert conf.server.max_content_length == 1048576
    assert not conf.security.auth.issuers


def test_service_info(client: FlaskClient) -> None:
    """FOCA serves the configured service info, with the package version."""
    response = client.get(f"{BASE_PATH}/service-info")
    assert response.status_code == 200
    service_info = response.get_json()
    assert service_info["name"] == "proTES"
    assert service_info["version"] == version("pro-tes")
    assert service_info["type"] == {
        "group": "org.ga4gh",
        "artifact": "tes",
        "version": "1.0.0",
    }
    assert service_info["storage"] == ["file:///path/to/local/storage"]


def test_service_info_not_stored(
    client: FlaskClient, mongo_client: mongomock.MongoClient
) -> None:
    """The service info is not stored in the database."""
    assert client.get(f"{BASE_PATH}/service-info").status_code == 200
    for db_name in mongo_client.list_database_names():
        assert "service_info" not in (
            mongo_client[db_name].list_collection_names()
        )


def test_middleware_api_not_registered(client: FlaskClient) -> None:
    """The middleware management API is not served (no controllers yet)."""
    assert client.get("/protes/v1/middlewares").status_code == 404


def test_error_problem_details(client: FlaskClient) -> None:
    """Errors are problem details, sent as `application/json`."""
    response = client.get(f"{BASE_PATH}/tasks/UNKNOWN")
    assert response.status_code == 404
    assert response.mimetype == "application/json"
    assert response.get_json() == {
        "title": "Not found",
        "status": 404,
        "detail": "The requested task wasn't found.",
    }


def test_request_body_size_limit(client: FlaskClient) -> None:
    """Request bodies larger than 1 MiB are rejected."""
    response = client.post(
        f"{BASE_PATH}/tasks",
        data=b"x" * (1048576 + 1),
        content_type="application/json",
    )
    assert response.status_code == 413
