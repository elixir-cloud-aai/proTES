"""Shared fixtures.

The tests create the app from `pro_tes/config.yaml` and have no network
access, except to the test database: TES backends are replaced by fakes
(e.g., `tests/ga4gh/tes/test_server.py`), and the hosts of task inputs and
TES backends are not resolved (distance-based task distribution).

The tests use an in-memory database (mongomock), or, if the environment
variable `MONGODB_TEST_URI` is set, the MongoDB at that URI (e.g.,
`mongodb://localhost:27017`), as in CI: there, the databases in `DBS` are
dropped before each test, so use a single-node MongoDB (only the hosts of the
URI may be connected to) without other data, one per test run. The tests
refuse to start if one of the databases exists already.
"""

from collections.abc import Iterator
import os
import socket
from typing import Any

from connexion import FlaskApp
from flask.testing import FlaskClient
from foca.database import register_mongodb
import mongomock
from pymongo import MongoClient, uri_parser
import pytest

from pro_tes.app import init_app
from pro_tes.plugins.middlewares.task_distribution import distance

# Database of the tasks (`db.dbs` of `config.yaml`)
DB = "taskStore"
# Database set via environment variable `MONGO_DBNAME` in tests
OTHER_DB = "protes"
# Databases that the tests use, and drop
DBS = (DB, OTHER_DB)
# URI of a MongoDB to test against instead of mongomock (see above)
MONGODB_TEST_URI = os.environ.get("MONGODB_TEST_URI", "")
_CONNECT = socket.socket.connect

# The Celery app (`pro_tes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced before the test modules are imported; the apps of the
# tests get the client of fixture `mongo_client`
_IMPORT_CLIENT: MongoClient[dict[str, Any]] = mongomock.MongoClient()


def _import_client(*args: Any, **kwargs: Any) -> MongoClient[dict[str, Any]]:
    """Get the in-memory client used while the test modules are imported."""
    return _IMPORT_CLIENT


register_mongodb.MongoClient = _import_client  # type: ignore[misc, assignment]


def _db_addresses(uri: str) -> frozenset[tuple[str, int]]:
    """Return the socket addresses of the hosts of a MongoDB URI.

    Args:
        uri: MongoDB connection string (`mongodb://`), or an empty string.

    Returns:
        IP address and port of each host of the URI; none for an empty string.
    """
    if not uri:
        return frozenset()
    return frozenset(
        (str(info[4][0]), port)
        for host, port in uri_parser.parse_uri(uri)["nodelist"]
        for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    )


# Addresses that the tests may connect to
DB_ADDRESSES = _db_addresses(MONGODB_TEST_URI)


def _no_network(sock: socket.socket, address: object) -> None:
    """Replace `socket.socket.connect()`, to block network access.

    Connections to the test database (`DB_ADDRESSES`) are allowed.

    Args:
        sock: Socket to connect.
        address: Address to connect to.

    Raises:
        OSError: If the address is not one of the test database.
    """
    if isinstance(address, tuple) and tuple(address[:2]) in DB_ADDRESSES:
        _CONNECT(sock, address)
        return
    raise OSError(f"Network access blocked in tests: {address}")


def _no_name_resolution(host: str) -> str:
    """Replace `gethostbyname()` of the distance-based task distribution.

    Name resolution doesn't go through `socket.socket.connect()`, so it is
    blocked separately.

    Args:
        host: Host name.

    Raises:
        socket.gaierror: Always.
    """
    raise socket.gaierror(f"Name resolution blocked in tests: {host}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block network access of all tests, except to the test database."""
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(distance, "gethostbyname", _no_name_resolution)


def _check_no_db(client: MongoClient[dict[str, Any]]) -> None:
    """Check that a MongoDB has none of the databases `DBS`.

    Args:
        client: Client of the MongoDB.

    Ends the test run (`pytest.exit()`, as a usage error) if one of the
    databases exists, e.g., with data of a deployment, or left by an aborted
    test run, as the tests would drop it.
    """
    existing = sorted(set(DBS) & set(client.list_database_names()))
    if existing:
        pytest.exit(
            f"MongoDB at MONGODB_TEST_URI has databases {existing}, which the"
            " tests would drop: use a MongoDB without them (drop them if they"
            " are left from an aborted test run)",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


@pytest.fixture(scope="session")
def _mongodb() -> Iterator[MongoClient[dict[str, Any]] | None]:
    """Client of the MongoDB at `MONGODB_TEST_URI`, if set, for all tests.

    Fails if the MongoDB has one of the databases `DBS` before the tests.
    """
    if not MONGODB_TEST_URI:
        yield None
        return
    client: MongoClient[dict[str, Any]] = MongoClient(
        MONGODB_TEST_URI, serverSelectionTimeoutMS=5000
    )
    try:
        _check_no_db(client)
        yield client
        for name in DBS:
            client.drop_database(name)
    finally:
        client.close()


@pytest.fixture
def mongo_client(
    monkeypatch: pytest.MonkeyPatch,
    _mongodb: MongoClient[dict[str, Any]] | None,
) -> MongoClient[dict[str, Any]]:
    """MongoDB client used by all apps created in a test.

    The MongoDB at `MONGODB_TEST_URI`, without the databases `DBS`, if set;
    otherwise a new in-memory one.
    """
    client: MongoClient[dict[str, Any]]
    if _mongodb is None:
        client = mongomock.MongoClient()
    else:
        client = _mongodb
        for name in DBS:
            client.drop_database(name)
    monkeypatch.setattr(register_mongodb, "MongoClient", lambda *args, **kwargs: client)
    return client


@pytest.fixture
def app(
    mongo_client: MongoClient[dict[str, Any]],
) -> FlaskApp:
    """App created from `config.yaml`."""
    return init_app()


@pytest.fixture
def client(app: FlaskApp) -> FlaskClient:
    """Test client of the app."""
    return app.app.test_client()
