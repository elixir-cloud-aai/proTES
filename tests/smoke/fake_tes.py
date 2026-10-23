"""Fake TES backend and identity provider for the Compose smoke test.

Serves the parts of the TES API that proTES uses (service info, task
creation, retrieval and cancellation) from memory: created tasks are
complete right away. Also serves an OpenID Connect identity provider for
proTES (issuer `ISSUER`; discovery, keys and user info), which issues access
tokens of the users of `GROUPS` at `GET /oidc/token?sub=<user>` (no login),
and access tokens of proTES itself at `POST /oidc/token` (client credentials
grant, with the client `CLIENT_ID` and `CLIENT_SECRET`). Except for the
service info, the TES API requires access tokens of proTES, as TES backends
of proTES may.
Run it with `python fake_tes.py [port]` (default 8080); it depends on the
standard library, PyJWT and cryptography (dependencies of FOCA).
"""

import base64
import binascii
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

BASE_PATH = "/ga4gh/tes/v1"
# Issuer of the fake identity provider, as proTES reaches it (`config.yaml` of
# this directory)
ISSUER = "http://fake-tes:8080/oidc/"
AUDIENCE = "smoke-client"
# Client of proTES (fictitious; as in `compose.yaml` of this directory)
CLIENT_ID = "smoke-protes"
CLIENT_SECRET = "smoke-protes-secret"  # noqa: S105 (fictitious)
# Groups of the users (user info), as mapped to roles in `config.yaml`
GROUPS = {
    "alice": ["smoke-users"],
    "admin": ["smoke-admins"],
    "eve": ["other-group"],
}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SERVICE_INFO = {
    "id": "org.example.fake-tes",
    "name": "Fake TES",
    "type": {"group": "org.ga4gh", "artifact": "tes", "version": "1.1.0"},
    "organization": {"name": "Example Organization", "url": "https://example.org"},
    "version": "1.0.0",
}
TASKS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    """Handler of requests to the fake TES API."""

    def _send(self, status: HTTPStatus, body: object) -> None:
        """Send a JSON response.

        Args:
            status: HTTP status.
            body: JSON body.
        """
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_idp(self, path: str) -> bool:
        """Serve the endpoints of the identity provider.

        Args:
            path: Request path.

        Returns:
            Whether the path is one of the identity provider.
        """
        if path == "/oidc/.well-known/openid-configuration":
            self._send(
                HTTPStatus.OK,
                {
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}jwk",
                    "token_endpoint": f"{ISSUER}token",
                    "userinfo_endpoint": f"{ISSUER}userinfo",
                },
            )
        elif path == "/oidc/jwk":
            key = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
            self._send(HTTPStatus.OK, {"keys": [{**key, "kid": "smoke", "use": "sig"}]})
        elif path == "/oidc/userinfo":
            token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            try:
                claims = jwt.decode(
                    token, KEY.public_key(), algorithms=["RS256"], audience=AUDIENCE
                )
            except jwt.InvalidTokenError:
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "invalid_token"})
                return True
            sub = claims["sub"]
            self._send(HTTPStatus.OK, {"sub": sub, "groups": GROUPS.get(sub, [])})
        elif path == "/oidc/token":
            sub = parse_qs(urlsplit(self.path).query).get("sub", ["alice"])[0]
            now = int(time.time())
            token = jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "sub": sub,
                    "iat": now,
                    "exp": now + 600,
                },
                KEY,
                algorithm="RS256",
                headers={"kid": "smoke", "typ": "at+jwt"},
            )
            self._send(HTTPStatus.OK, {"access_token": token})
        else:
            return False
        return True

    def _issue_client_token(self, body: bytes) -> None:
        """Issue an access token of proTES (client credentials grant).

        The client authenticates with HTTP Basic (`client_secret_basic`).

        Args:
            body: Request body (form-encoded).
        """
        form = parse_qs(body.decode())
        header = self.headers.get("Authorization") or ""
        scheme, _, credentials = header.partition(" ")
        try:
            decoded = base64.b64decode(credentials, validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            decoded = ""
        client = tuple(unquote_plus(value) for value in decoded.split(":", 1))
        if form.get("grant_type") != ["client_credentials"]:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "unsupported_grant_type"})
            return
        if scheme != "Basic" or client != (CLIENT_ID, CLIENT_SECRET):
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "invalid_client"})
            return
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": CLIENT_ID,
                "client_id": CLIENT_ID,
                "iat": now,
                "exp": now + 300,
            },
            KEY,
            algorithm="RS256",
            headers={"kid": "smoke", "typ": "at+jwt"},
        )
        self._send(
            HTTPStatus.OK,
            {"access_token": token, "token_type": "Bearer", "expires_in": 300},
        )

    def _authorized(self) -> bool:
        """Check that the request has an access token of proTES.

        Sends `401` if not.

        Returns:
            Whether the request has a valid access token of proTES.
        """
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(
                token,
                KEY.public_key(),
                algorithms=["RS256"],
                issuer=ISSUER,
                options={"verify_aud": False},
            )
        except jwt.InvalidTokenError:
            claims = {}
        if claims.get("client_id") != CLIENT_ID:
            self._send(HTTPStatus.UNAUTHORIZED, {"msg": "token of proTES required"})
            return False
        return True

    def do_GET(self) -> None:
        """Serve the service info, tasks, the list of tasks, and the IdP."""
        path = self.path.split("?", 1)[0]
        if self._serve_idp(path):
            return
        if path == f"{BASE_PATH}/service-info":
            self._send(HTTPStatus.OK, SERVICE_INFO)
            return
        if not self._authorized():
            return
        if path == f"{BASE_PATH}/tasks":
            with LOCK:
                tasks = [
                    {"id": key, "state": task["state"]} for key, task in TASKS.items()
                ]
            self._send(HTTPStatus.OK, {"tasks": tasks})
            return
        task_id = path.removeprefix(f"{BASE_PATH}/tasks/")
        with LOCK:
            task = TASKS.get(task_id)
        if task is None or task_id == path:
            self._send(HTTPStatus.NOT_FOUND, {"msg": "not found"})
            return
        self._send(HTTPStatus.OK, task)

    def do_POST(self) -> None:
        """Create tasks (complete right away), cancel them (no-op), issue tokens."""
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if path == "/oidc/token":
            self._issue_client_token(body)
            return
        if not self._authorized():
            return
        if path == f"{BASE_PATH}/tasks":
            try:
                task = json.loads(body)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, {"msg": "invalid JSON"})
                return
            with LOCK:
                task_id = f"fake-{len(TASKS) + 1}"
                TASKS[task_id] = {
                    **task,
                    "id": task_id,
                    "state": "COMPLETE",
                    "logs": [{"logs": [], "outputs": []}],
                }
            self._send(HTTPStatus.OK, {"id": task_id})
            return
        if path.startswith(f"{BASE_PATH}/tasks/") and path.endswith(":cancel"):
            self._send(HTTPStatus.OK, {})
            return
        self._send(HTTPStatus.NOT_FOUND, {"msg": "not found"})


def main() -> None:
    """Serve the fake TES API on all interfaces (in a container)."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104
    server.serve_forever()


if __name__ == "__main__":
    main()
