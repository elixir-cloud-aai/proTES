"""Tests of the database connector and migrations."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from foca import Foca
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure
import pytest

from pro_tes import celery_worker
from pro_tes.app import init_app
from pro_tes.utils import db as db_module
from pro_tes.utils.db import (
    LEGACY_TASK_INDEX,
    DbDocumentConnector,
    check_unique_task_ids,
    drop_legacy_task_index,
    find_duplicate_task_ids,
    remove_stored_credentials,
)
from tests.conftest import DB, set_overlays


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


def test_update_task_state_if_state(connector: DbDocumentConnector) -> None:
    """States are updated only if they are the expected one, if one is given."""
    assert not connector.update_task_state(state="QUEUED", if_state="UNKNOWN")
    assert connector.update_task_state(state="QUEUED", if_state="invalid")
    assert connector.update_task_state(state="RUNNING")
    document = connector.collection.find_one({"worker_id": "worker-1"})
    assert document is not None
    assert document["task"]["state"] == "RUNNING"


def test_update_task_state_if_states(connector: DbDocumentConnector) -> None:
    """States are updated only if they are one of the expected ones."""
    assert not connector.update_task_state(
        state="QUEUED", if_state=["UNKNOWN", "RUNNING"]
    )
    # none expected: never updated
    assert not connector.update_task_state(state="QUEUED", if_state=[])
    # the stored state of the fixture is `invalid`
    assert connector.update_task_state(state="QUEUED", if_state=["UNKNOWN", "invalid"])
    document = connector.collection.find_one({"worker_id": "worker-1"})
    assert document is not None
    assert document["task"]["state"] == "QUEUED"


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
            {"task": {"id": {"x": 1}}},
            {"task": {"id": {"x": 1}}},
        ]
    )
    assert find_duplicate_task_ids(collection=collection) == {
        "A": 3,
        "B": 2,
        None: 2,
        "{'x': 1}": 2,
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


@pytest.mark.parametrize(
    ("max_reported", "reported"),
    [
        (10, "tasks per identifier: 'A': 3, 'B': 2, (none): 2)"),
        (1, "tasks per identifier: 'A': 3, and 2 more)"),
    ],
)
def test_duplicate_task_ids(
    app_env: None,
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    max_reported: int,
    reported: str,
) -> None:
    """The app refuses to start while stored tasks share identifiers (#248).

    The identifiers are named (up to `MAX_REPORTED_DUPLICATES`), and no task
    is changed.
    """
    monkeypatch.setattr(db_module, "MAX_REPORTED_DUPLICATES", max_reported)
    collection = mongo_client[DB]["tasks"]
    documents: list[dict[str, Any]] = [
        *[{"task": {"id": "A"}, "user_id": f"user-{n}"} for n in range(3)],
        *[{"task": {"id": "B"}, "user_id": f"user-{n}"} for n in range(2)],
        {"task": {"id": "C"}},
        *[{"worker_id": f"worker-{n}"} for n in range(2)],
    ]
    collection.insert_many(deepcopy(documents))
    with pytest.raises(RuntimeError, match="share 3 identifiers") as exc_info:
        init_app()
    assert reported in str(exc_info.value)
    assert "upgrading notes" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)
    stored = list(collection.find({}, projection={"_id": False}))
    assert sorted(map(str, stored)) == sorted(map(str, documents))


# Overlay with a collection with a unique index other than that of tasks
OTHER_UNIQUE_INDEX = """
db:
  dbs:
    taskStore:
      collections:
        others:
          indexes:
            - keys:
                name: 1
              options:
                unique: true
"""


@pytest.fixture
def other_unique_index(
    app_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Apps get a collection `others`, with a unique index on `name`."""
    overlay = tmp_path / "other_unique_index.yaml"
    overlay.write_text(OTHER_UNIQUE_INDEX)
    set_overlays(monkeypatch, overlay)


@pytest.mark.parametrize(
    ("collection", "error", "message"),
    [
        ("tasks", RuntimeError, "share 1 identifiers"),
        ("others", ValueError, "conflicts with an existing index"),
    ],
)
def test_duplicate_keys_celery_worker(
    other_unique_index: None,
    mongo_client: MongoClient[dict[str, Any]],
    collection: str,
    error: type[Exception],
    message: str,
) -> None:
    """Celery workers refuse to start while tasks share identifiers, too.

    Other failing unique indexes fail the start with FOCA's error.
    """
    mongo_client[DB][collection].insert_many(
        [{"task": {"id": "A"}, "name": "a"} for _ in range(2)]
    )
    foca = Foca(
        config_file=Path(celery_worker.__file__).parent / "config.yaml",
        custom_config_model="pro_tes.config_models.CustomConfig",
    )
    with pytest.raises(error, match=message):
        celery_worker.create_celery_app(foca=foca)


@pytest.mark.parametrize("dropped", [True, False])
def test_legacy_task_index_dropped_concurrently(
    mongo_client: MongoClient[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    dropped: bool,
) -> None:
    """Indexes dropped by other instances meanwhile are no error.

    Other errors are raised.
    """
    collection = mongo_client[DB]["tasks"]
    collection.create_index(
        [("task_id", 1), ("worker_id", 1)], unique=True, sparse=True
    )
    drop_index = collection.drop_index

    def drop_index_raced(name: str) -> None:
        """Fail to drop the index, which another instance may have dropped.

        Args:
            name: Name of the index.

        Raises:
            pymongo.errors.OperationFailure: Always.
        """
        if dropped:
            drop_index(name)
        raise OperationFailure("index not found", code=27)

    monkeypatch.setattr(collection, "drop_index", drop_index_raced)
    if dropped:
        drop_legacy_task_index(collection=collection)
    else:
        with pytest.raises(OperationFailure):
            drop_legacy_task_index(collection=collection)
    assert (LEGACY_TASK_INDEX in collection.index_information()) is not dropped


def test_duplicate_other_keys(
    other_unique_index: None, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """Other unique indexes that fail fail the start with FOCA's error."""
    mongo_client[DB]["others"].insert_many([{"name": "a"}, {"name": "a"}])
    with pytest.raises(ValueError, match="conflicts with an existing index"):
        init_app()


@pytest.mark.parametrize(
    "db",
    [
        None,
        SimpleNamespace(dbs=None),
        SimpleNamespace(dbs={}),
        SimpleNamespace(dbs={"taskStore": SimpleNamespace(collections=None)}),
        SimpleNamespace(
            dbs={
                "taskStore": SimpleNamespace(
                    collections={"tasks": SimpleNamespace(client=None)}
                )
            }
        ),
    ],
)
def test_check_unique_task_ids_no_collection(db: SimpleNamespace | None) -> None:
    """Without a registered task collection, nothing is reported."""
    exc = ValueError("index failed")
    exc.__cause__ = DuplicateKeyError("duplicate")
    foca = SimpleNamespace(conf=SimpleNamespace(db=db))
    check_unique_task_ids(foca=foca, exc=exc)  # type: ignore[arg-type]


def test_check_unique_task_ids_other_error() -> None:
    """Errors other than duplicate keys are not checked."""
    foca = SimpleNamespace(conf=None)
    check_unique_task_ids(foca=foca, exc=ValueError("other"))  # type: ignore[arg-type]
