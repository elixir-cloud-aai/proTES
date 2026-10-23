"""Tests of the database connector."""

from typing import Any

from pymongo import MongoClient
import pytest

from pro_tes.app import init_app
from pro_tes.utils.db import (
    LEGACY_TASK_INDEX,
    DbDocumentConnector,
    drop_legacy_task_index,
    find_duplicate_task_ids,
    remove_stored_credentials,
)
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


def files(*urls: str | None) -> list[dict[str, Any]]:
    """Get stored inputs or outputs.

    Args:
        *urls: URLs of the inputs or outputs; `None` for an input with
            content.

    Returns:
        Inputs or outputs, as stored.
    """
    return [
        {"url": url, "path": f"/{index}"} if url else {"content": "x", "path": "/c"}
        for index, url in enumerate(urls)
    ]


def test_remove_stored_credentials(
    mongo_client: MongoClient[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Basic credentials and credentials in URLs are removed from stored tasks.

    Other URLs, and tasks without credentials, are kept as they are;
    repeated runs change nothing.
    """
    collection = mongo_client[DB]["tasks"]
    task = {
        "inputs": files("s3://key:secret@bucket/in", None, "https://[invalid/x"),
        # leading whitespace, which URL parsing ignores
        "outputs": files("ftp://ftp.example.org/a@b", " \thttps://u:p@bucket/ws"),
    }
    original = {"inputs": files("https://user@example.org/in"), "outputs": "invalid"}
    collection.insert_many(
        [
            {
                "worker_id": "worker-1",
                "basic_auth": {"username": "user", "password": "secret"},
                "task": task,
                "task_original": original,
            },
            {"worker_id": "worker-2", "basic_auth": {}, "task": {}},
            {"worker_id": "worker-3", "task": {"outputs": files("s3://bucket/out")}},
            {
                "worker_id": "worker-4",
                "task": {
                    "logs": [
                        "invalid",
                        {"outputs": files("https://user:pw@bucket/out")},
                        {"outputs": files("s3://bucket/out")},
                    ]
                },
            },
        ]
    )
    with caplog.at_level("INFO"):
        remove_stored_credentials(collection=collection)
    assert "Removed HTTP Basic credentials from 2 stored tasks" in caplog.text
    assert "Removed credentials from the URLs of 2 stored tasks" in caplog.text
    documents = {
        document.pop("worker_id"): document
        for document in collection.find(projection={"_id": False})
    }
    assert documents == {
        "worker-1": {
            "task": {
                "inputs": files("s3://bucket/in", None, "https://[invalid/x"),
                "outputs": files("ftp://ftp.example.org/a@b", "https://bucket/ws"),
            },
            "task_original": {
                "inputs": files("https://example.org/in"),
                "outputs": "invalid",
            },
        },
        "worker-2": {"task": {}},
        "worker-3": {"task": {"outputs": files("s3://bucket/out")}},
        "worker-4": {
            "task": {
                "logs": [
                    "invalid",
                    {"outputs": files("https://bucket/out")},
                    {"outputs": files("s3://bucket/out")},
                ]
            }
        },
    }
    caplog.clear()
    with caplog.at_level("INFO"):
        remove_stored_credentials(collection=collection)
    assert "Removed" not in caplog.text


def test_remove_stored_credentials_on_startup(
    app_env: None, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Credentials of stored tasks are removed when the app starts."""
    collection = mongo_client[DB]["tasks"]
    collection.insert_one({"worker_id": "worker-1", "basic_auth": {"password": "x"}})
    init_app()
    document = collection.find_one({"worker_id": "worker-1"})
    assert document is not None
    assert "basic_auth" not in document


def test_find_duplicate_task_ids(mongo_client: MongoClient[dict[str, Any]]) -> None:
    """Shared task identifiers are found, also of tasks without identifier."""
    collection = mongo_client[DB]["tasks"]
    collection.insert_many(
        [
            {"task": {"id": "B"}},
            {"task": {"id": "B"}},
            {"task": {"id": "A"}},
            {"task": {"id": "A"}},
            {"task": {"id": "A"}},
            {"task": {"id": "C"}},
            {"task": {}},
            {"worker_id": "worker-1"},
        ]
    )
    assert find_duplicate_task_ids(collection=collection) == {
        "A": 3,
        "B": 2,
        None: 2,
    }


def test_find_duplicate_task_ids_none(
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Unique task identifiers are not reported."""
    collection = mongo_client[DB]["tasks"]
    collection.insert_many([{"task": {"id": "A"}}, {"task": {"id": "B"}}])
    assert find_duplicate_task_ids(collection=collection) == {}


def test_legacy_task_index_dropped(
    app_env: None,
    mongo_client: MongoClient[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The index of earlier versions is dropped when the app starts, once."""
    collection = mongo_client[DB]["tasks"]
    collection.create_index(
        [("task_id", 1), ("worker_id", 1)], unique=True, sparse=True
    )
    assert LEGACY_TASK_INDEX in collection.index_information()
    init_app()
    indexes = collection.index_information()
    assert LEGACY_TASK_INDEX not in indexes
    assert f"Dropped index '{LEGACY_TASK_INDEX}'" in capsys.readouterr().err
    drop_legacy_task_index(collection=collection)
    assert collection.index_information() == indexes
    assert "Dropped index" not in capsys.readouterr().err
