"""Shared fixtures.

The tests create the app from `pro_tes/config.yaml` and the test overlay
(`tests/config_overlay.yaml`, with the settings each deployment must make),
and have no network access, except to the test database: the identity
provider is mocked, TES backends are replaced by fakes (e.g.,
`tests/ga4gh/tes/test_server.py`), and the hosts of task inputs and TES
backends are not resolved (distance-based task distribution). The test
client sends the access token of a user (`USER`) with each request.

The tests use an in-memory database (mongomock), or, if the environment
variable `MONGODB_TEST_URI` is set, the MongoDB at that URI (e.g.,
`mongodb://localhost:27017`), as in CI: there, the databases in `DBS` are
dropped before each test, so use a single-node MongoDB (only the hosts of the
URI may be connected to; a standalone server, not a replica set, whose
members would be announced under other names) without other data, one per
test run. The tests
refuse to start if one of the databases exists already.
"""

from collections.abc import Iterator
import errno
import os
from pathlib import Path
import socket
import time
from typing import Any

from connexion import FlaskApp
from cryptography.hazmat.primitives.asymmetric import rsa
from flask.testing import FlaskClient
from foca.database import register_mongodb
from foca.security import auth
import jwt
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
_CONNECT_EX = socket.socket.connect_ex
# Config overlay with the settings each deployment must make (issuer,
# audience, groups of the roles)
CONFIG_OVERLAY = Path(__file__).parent / "config_overlay.yaml"
AUDIENCE = "client-id"
# The trusted issuer of the test overlay; all its endpoints are mocked
ISSUER = "https://login.example.org/oidc/"
JWKS_URI = f"{ISSUER}jwk"
USERINFO_URI = f"{ISSUER}userinfo"
USER = "alice@example.org"
# Another user with the role `user`
OTHER_USER = "bob@example.org"
ADMIN = "admin@example.org"
# Admin with the role `user`, too
ADMIN_USER = "carol@example.org"
# Member of no group with a role
OUTSIDER = "eve@example.org"
# Groups mapped to the roles `user` and `admin` in the test overlay
USER_GROUP = "compute-users"
ADMIN_GROUP = "urn:example:group:compute-admins#login.example.org"
# Group memberships the identity provider returns as user info (not in
# access tokens)
GROUPS = {
    USER: [USER_GROUP],
    OTHER_USER: [USER_GROUP],
    ADMIN: [ADMIN_GROUP],
    ADMIN_USER: [USER_GROUP, ADMIN_GROUP],
    OUTSIDER: ["other-group"],
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# The Celery app (`pro_tes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced before the test modules are imported; the apps of the
# tests get the client of fixture `mongo_client`
_IMPORT_CLIENT: MongoClient[dict[str, Any]] = mongomock.MongoClient()


def _import_client(*args: Any, **kwargs: Any) -> MongoClient[dict[str, Any]]:
    """Get the in-memory client used while the test modules are imported."""
    return _IMPORT_CLIENT


register_mongodb.MongoClient = _import_client  # type: ignore[misc, assignment]
# Its config needs the settings of the test overlay, too (FOCA refuses access
# control without trusted issuers); fixture `app_env` sets them for the apps
os.environ["FOCA_CONFIG_OVERLAY"] = str(CONFIG_OVERLAY)


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


def _no_network_ex(sock: socket.socket, address: object) -> int:
    """Replace `socket.socket.connect_ex()`, to block network access.

    Connections to the test database (`DB_ADDRESSES`) are allowed.

    Args:
        sock: Socket to connect.
        address: Address to connect to.

    Returns:
        The result of the connection to the test database; `EACCES` for
        other addresses.
    """
    if isinstance(address, tuple) and tuple(address[:2]) in DB_ADDRESSES:
        return _CONNECT_EX(sock, address)
    return errno.EACCES


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
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network_ex)
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
    otherwise a new in-memory one. `MONGO_DBNAME` of the environment is
    unset, so that the apps use database `DB` (unless a test sets it).
    """
    monkeypatch.delenv("MONGO_DBNAME", raising=False)
    client: MongoClient[dict[str, Any]]
    if _mongodb is None:
        client = mongomock.MongoClient()
    else:
        client = _mongodb
        for name in DBS:
            client.drop_database(name)
    monkeypatch.setattr(register_mongodb, "MongoClient", lambda *args, **kwargs: client)
    return client


class MockResponse:
    """Mock response of `requests.get()`."""

    def __init__(self, data: dict[str, Any], status_code: int = 200) -> None:
        """Create a response.

        Args:
            data: JSON body.
            status_code: HTTP status code.
        """
        self.data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise an error for error status codes, as `requests` does.

        Raises:
            requests.HTTPError: The status code is 400 or higher.
        """
        if self.status_code >= 400:
            raise auth.requests.HTTPError(str(self.status_code))

    def json(self) -> dict[str, Any]:
        """Get the JSON body.

        Returns:
            The JSON body.
        """
        return self.data


def mock_get(
    url: str, headers: dict[str, str] | None = None, **kwargs: Any
) -> MockResponse:
    """Mock `requests.get()` for the identity provider.

    Serves the discovery, JWK set and user info endpoints; other requests
    fail.

    Args:
        url: Requested URL.
        headers: Request headers; with an access token for user info.
        kwargs: Other arguments of `requests.get()`; ignored.

    Returns:
        Response of the endpoint; status `401` for user info requests with
        an invalid access token.

    Raises:
        requests.ConnectionError: The URL is not an endpoint of the identity
            provider.
    """
    if url.rstrip("/") == f"{ISSUER}.well-known/openid-configuration":
        return MockResponse(
            {
                "issuer": ISSUER,
                "jwks_uri": JWKS_URI,
                "userinfo_endpoint": USERINFO_URI,
            }
        )
    if url == JWKS_URI:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
        return MockResponse({"keys": [{**jwk, "kid": "rsa1", "use": "sig"}]})
    if url == USERINFO_URI:
        assert headers is not None
        token = headers["Authorization"].removeprefix("Bearer ")
        try:
            claims = jwt.decode(
                token, KEY.public_key(), algorithms=["RS256"], audience=AUDIENCE
            )
        except jwt.InvalidTokenError:
            return MockResponse({"error": "invalid_token"}, status_code=401)
        sub = claims["sub"]
        return MockResponse({"sub": sub, "groups": GROUPS.get(sub, [])})
    raise auth.requests.ConnectionError(f"Unexpected request in tests: {url}")


def make_token(
    user: str = USER,
    key: rsa.RSAPrivateKey = KEY,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    typ: str = "at+jwt",
    claims: dict[str, Any] | None = None,
) -> str:
    """Get an access token of a user.

    Args:
        user: User identifier (`sub` claim).
        key: Key that signs the token.
        issuer: Issuer of the token (`iss` claim).
        audience: Audience of the token (`aud` claim).
        typ: Type of the token (`typ` header), `at+jwt` for access
            tokens (RFC 9068); e.g., `JWT` for ID tokens.
        claims: Other claims, e.g., `client_id`.

    Returns:
        Access token.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "aud": audience,
            "sub": user,
            "iss": issuer,
            "iat": now,
            "exp": now + 300,
            **(claims or {}),
        },
        key,
        algorithm="RS256",
        headers={"kid": "rsa1", "typ": typ},
    )


def make_headers(user: str = USER, **kwargs: Any) -> dict[str, str]:
    """Get request headers with an access token of a user.

    Args:
        user: User identifier (`sub` claim).
        **kwargs: Other arguments of :py:func:`make_token`.

    Returns:
        Request headers.
    """
    return {"Authorization": f"Bearer {make_token(user=user, **kwargs)}"}


def _clear_auth_caches() -> None:
    """Clear FOCA's caches of the identity provider's responses."""
    auth._oidc_configs.clear()
    auth._jwk_sets.clear()
    auth._failed_fetches.clear()
    auth._userinfo_cache.clear()


@pytest.fixture
def mock_idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mock identity provider."""
    monkeypatch.setattr(auth.requests, "get", mock_get)
    _clear_auth_caches()
    yield
    _clear_auth_caches()


@pytest.fixture
def app_env(
    monkeypatch: pytest.MonkeyPatch,
    mongo_client: MongoClient[dict[str, Any]],
    mock_idp: None,
) -> None:
    """Environment for creating the app: the test overlay and database.

    Tests with overlays of their own list them after `CONFIG_OVERLAY`, see
    :py:func:`set_overlays`.
    """
    set_overlays(monkeypatch)


def set_overlays(monkeypatch: pytest.MonkeyPatch, *overlays: Path) -> None:
    """Create apps from `config.yaml`, the test overlay and other overlays.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        *overlays: Config overlays applied after the test overlay.
    """
    monkeypatch.setenv(
        "FOCA_CONFIG_OVERLAY",
        os.pathsep.join(str(path) for path in (CONFIG_OVERLAY, *overlays)),
    )


@pytest.fixture
def app(app_env: None) -> FlaskApp:
    """App created from `config.yaml` and the test overlay."""
    return init_app()


@pytest.fixture
def client(app: FlaskApp) -> FlaskClient:
    """Test client of the app, sending the access token of `USER`."""
    client = app.app.test_client()
    client.environ_base["HTTP_AUTHORIZATION"] = make_headers()["Authorization"]
    return client


@pytest.fixture
def anonymous_client(app: FlaskApp) -> FlaskClient:
    """Test client of the app, without access token."""
    return app.app.test_client()
