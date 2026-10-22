"""Fake TES backend for the Compose smoke test.

Serves the parts of the TES API that proTES uses (service info, task
creation, retrieval and cancellation) from memory: created tasks are
complete right away. Run it with `python fake_tes.py [port]` (default 8080);
it only depends on the standard library.
"""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
from typing import Any

BASE_PATH = "/ga4gh/tes/v1"
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

    def do_GET(self) -> None:
        """Serve the service info, tasks, and the list of tasks."""
        path = self.path.split("?", 1)[0]
        if path == f"{BASE_PATH}/service-info":
            self._send(HTTPStatus.OK, SERVICE_INFO)
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
        """Create tasks (complete right away), and cancel them (no-op)."""
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
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
