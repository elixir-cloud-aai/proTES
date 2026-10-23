"""Tests of the database connector."""

from typing import Any

from pymongo import MongoClient
import pytest

from pro_tes.utils.db import DbDocumentConnector
from tests.conftest import DB


@pytest.fixture
def connector(mongo_client: MongoClient[dict[str, Any]]) -> DbDocumentConnector:
    """Connector to a stored document that doesn't conform to the schema."""
    collection = mongo_client[DB]["tasks"]
    collection.insert_one({"worker_id": "worker-1", "task": {"state": "invalid"}})
    return DbDocumentConnector(collection=collection, worker_id="worker-1")


def test_get_document_invalid(connector: DbDocumentConnector) -> None:
    """Documents that don't conform to the schema are reported."""
    with pytest.raises(ValueError, match="does not conform to schema"):
        connector.get_document()


def test_upsert_invalid(connector: DbDocumentConnector) -> None:
    """Updated documents that don't conform to the schema are reported."""
    with pytest.raises(ValueError, match="does not conform to schema"):
        connector.upsert_fields_in_root_object(root="tes_endpoint", host="x")


def test_update_task_state_unknown(connector: DbDocumentConnector) -> None:
    """Unknown states are rejected, and not stored."""
    with pytest.raises(ValueError, match="Unknown state: DONE"):
        connector.update_task_state(state="DONE")
    document = connector.collection.find_one({"worker_id": "worker-1"})
    assert document is not None
    assert document["task"]["state"] == "invalid"
