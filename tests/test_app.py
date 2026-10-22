"""Tests of the app created from `config.yaml`, with FOCA 0.15."""

# pylint: disable=redefined-outer-name

from importlib.metadata import version
from pathlib import Path
from typing import Any

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca import get_config
from pymongo import MongoClient
import pytest
import yaml

from pro_tes.app import init_app

BASE_PATH = "/ga4gh/tes/v1"


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
    assert service_info["id"] == "org.example.protes"
    assert service_info["organization"] == {
        "name": "Example Organization",
        "url": "https://example.org",
    }
    assert "storage" not in service_info


def test_service_info_not_stored(
    client: FlaskClient, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """The service info is not stored in the database."""
    assert client.get(f"{BASE_PATH}/service-info").status_code == 200
    for db_name in mongo_client.list_database_names():
        assert "service_info" not in (mongo_client[db_name].list_collection_names())


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


@pytest.mark.parametrize(
    "service, fields",
    [
        ({}, "`id`, `organization`"),
        ({"id": "org.example.tes-proxy"}, "`organization`"),
        ({"organization": {"name": "My org"}}, "`id`, `organization`"),
        (
            {"organization": {"name": "My org", "url": "https://my.example.org"}},
            "`id`",
        ),
        (
            {
                "id": "org.example.tes-proxy",
                "organization": {"name": "My org", "url": "https://my.example.org"},
            },
            None,
        ),
    ],
)
def test_placeholder_service_info(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mongo_client: MongoClient[dict[str, Any]],  # pylint: disable=unused-argument
    capsys: pytest.CaptureFixture[str],
    service: dict[str, Any],
    fields: str | None,
) -> None:
    """The app warns of placeholders in the service info, naming the fields.

    The fields are `id`, and `organization` while its name or URL is the
    placeholder's; there is no warning once both are set.
    """
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        yaml.safe_dump({"service_info": {"service": service}}), encoding="utf-8"
    )
    monkeypatch.setenv("FOCA_CONFIG_OVERLAY", str(overlay))
    init_app()
    # FOCA configures logging to stderr
    err = capsys.readouterr().err
    if fields is None:
        assert "placeholder values" not in err
    else:
        assert err.count("placeholder values") == 1
        assert f"placeholder values; set {fields} to" in err
