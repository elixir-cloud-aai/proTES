"""Tests of the shared fixtures (`tests/conftest.py`)."""

import errno
import os
import socket
from typing import Any

from connexion import FlaskApp
from foca import get_collection
import mongomock
from pymongo import MongoClient
import pytest

from pro_tes.plugins.middlewares.task_distribution import distance
from tests import conftest
from tests.conftest import DB, DBS


def test_mongo_client(mongo_client: MongoClient[dict[str, Any]]) -> None:
    """Tests use MongoDB at `MONGODB_TEST_URI` if it is set, else mongomock."""
    uri = os.environ.get("MONGODB_TEST_URI")
    assert isinstance(mongo_client, mongomock.MongoClient) is not bool(uri)
    if uri:
        assert mongo_client.server_info()["version"]


def test_app_uses_mongo_client(
    app: FlaskApp, mongo_client: MongoClient[dict[str, Any]]
) -> None:
    """The app stores tasks with the client of fixture `mongo_client`."""
    with app.app.app_context():
        get_collection(DB, "tasks").insert_one({"worker_id": "fixture"})
    assert mongo_client[DB]["tasks"].count_documents({"worker_id": "fixture"}) == 1


@pytest.mark.parametrize("run", [1, 2])
def test_mongo_client_empty(
    mongo_client: MongoClient[dict[str, Any]], run: int
) -> None:
    """Each test starts without the databases `DBS`.

    Meaningful with a MongoDB (`MONGODB_TEST_URI`), which all tests share:
    the second run would find the document of the first.
    """
    for name in DBS:
        tasks = mongo_client[name]["tasks"]
        assert tasks.count_documents({}) == 0
        tasks.insert_one({"worker_id": f"run{run}"})


@pytest.mark.parametrize("name", DBS)
def test_check_no_db(name: str) -> None:
    """MongoDBs with one of the databases `DBS` are refused.

    The tests would drop them.
    """
    client: MongoClient[dict[str, Any]] = mongomock.MongoClient()
    client["other"]["tasks"].insert_one({"worker_id": "other"})
    conftest._check_no_db(client)
    client[name]["tasks"].insert_one({"worker_id": "deployed"})
    with pytest.raises(pytest.exit.Exception, match=f"has databases \\['{name}'\\]"):
        conftest._check_no_db(client)


def test_db_addresses() -> None:
    """The addresses of a URI's hosts are resolved; default port 27017."""
    assert conftest._db_addresses("") == frozenset()
    assert conftest._db_addresses(
        "mongodb://127.0.0.1:12345,127.0.0.2/?replicaSet=rs"
    ) == {
        ("127.0.0.1", 12345),
        ("127.0.0.2", 27017),
    }


def test_no_network() -> None:
    """Connections other than to the test database are blocked."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(OSError, match="Network access blocked in tests"),
    ):
        sock.connect(("192.0.2.1", 443))


def test_no_network_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connections to the test database are passed on."""
    calls: list[object] = []
    monkeypatch.setattr(conftest, "DB_ADDRESSES", frozenset({("127.0.0.1", 12345)}))
    monkeypatch.setattr(
        conftest, "_CONNECT", lambda sock, address: calls.append(address)
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        conftest._no_network(sock, ("127.0.0.1", 12345))
        with pytest.raises(OSError, match="Network access blocked in tests"):
            conftest._no_network(sock, ("127.0.0.1", 12346))
        with pytest.raises(OSError, match="Network access blocked in tests"):
            conftest._no_network(sock, "/tmp/socket")  # noqa: S108 (not created)
    assert calls == [("127.0.0.1", 12345)]


def test_no_network_ex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connections via `connect_ex()` are blocked too, except to the database."""
    monkeypatch.setattr(conftest, "DB_ADDRESSES", frozenset({("127.0.0.1", 12345)}))
    monkeypatch.setattr(conftest, "_CONNECT_EX", lambda sock, address: 0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        assert sock.connect_ex(("192.0.2.1", 443)) == errno.EACCES
        assert sock.connect_ex(("127.0.0.1", 12345)) == 0


def test_no_name_resolution() -> None:
    """Hosts of task inputs and TES backends are not resolved."""
    with pytest.raises(socket.gaierror, match="Name resolution blocked in tests"):
        distance.gethostbyname("tes.example.org")
