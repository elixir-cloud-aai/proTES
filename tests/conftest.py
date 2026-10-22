"""Shared fixtures.

The app is created from `pro_tes/config.yaml`, with an in-memory database
(mongomock) instead of MongoDB.
"""

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca.database import register_mongodb
import mongomock
import pytest

from pro_tes.app import init_app

# The Celery app (`pro_tes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced before the test modules are imported
_IMPORT_CLIENT: mongomock.MongoClient = mongomock.MongoClient()
register_mongodb.MongoClient = (  # type: ignore[misc]
    lambda *args, **kwargs: _IMPORT_CLIENT
)


@pytest.fixture
def mongo_client(monkeypatch: pytest.MonkeyPatch) -> mongomock.MongoClient:
    """In-memory MongoDB client, used by all apps created in a test."""
    client: mongomock.MongoClient = mongomock.MongoClient()
    monkeypatch.setattr(register_mongodb, "MongoClient", lambda *args, **kwargs: client)
    return client


@pytest.fixture
def app(
    mongo_client: mongomock.MongoClient,  # pylint: disable=unused-argument
) -> FlaskApp:
    """App created from `config.yaml`."""
    return init_app()


@pytest.fixture
def client(app: FlaskApp) -> FlaskClient:  # pylint: disable=redefined-outer-name
    """Test client of the app."""
    return app.app.test_client()
