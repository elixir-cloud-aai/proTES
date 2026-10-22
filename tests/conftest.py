"""Shared fixtures.

The app is created from `pro_tes/config.yaml`, with an in-memory database
(mongomock) instead of MongoDB.
"""

from typing import Any

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca.database import register_mongodb
import mongomock
from pymongo import MongoClient
import pytest

from pro_tes.app import init_app

# The Celery app (`pro_tes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced before the test modules are imported
_IMPORT_CLIENT: MongoClient[dict[str, Any]] = mongomock.MongoClient()


def _import_client(*args: Any, **kwargs: Any) -> MongoClient[dict[str, Any]]:
    """Get the in-memory client used while the test modules are imported."""
    return _IMPORT_CLIENT


register_mongodb.MongoClient = _import_client  # type: ignore[misc, assignment]


@pytest.fixture
def mongo_client(monkeypatch: pytest.MonkeyPatch) -> MongoClient[dict[str, Any]]:
    """In-memory MongoDB client, used by all apps created in a test."""
    client: MongoClient[dict[str, Any]] = mongomock.MongoClient()
    monkeypatch.setattr(register_mongodb, "MongoClient", lambda *args, **kwargs: client)
    return client


@pytest.fixture
def app(
    mongo_client: MongoClient[dict[str, Any]],  # pylint: disable=unused-argument
) -> FlaskApp:
    """App created from `config.yaml`."""
    return init_app()


@pytest.fixture
def client(app: FlaskApp) -> FlaskClient:  # pylint: disable=redefined-outer-name
    """Test client of the app."""
    return app.app.test_client()
