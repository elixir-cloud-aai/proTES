"""Gunicorn settings of the image (production WSGI server).

The image runs ``gunicorn --config python:pro_tes.gunicorn_config
'pro_tes.app:init_app()'``. Override the settings via ``GUNICORN_CMD_ARGS``
(e.g., ``--workers 2 --threads 8``), which takes precedence over this module
(cf. https://gunicorn.org/reference/settings/).

The app is not preloaded (no ``preload_app``): each worker creates it after
forking, as MongoDB clients must not be shared across forks.

``X-Forwarded-*`` headers are trusted from the local host only (Gunicorn's
default); behind a reverse proxy, set its addresses via environment variable
``FORWARDED_ALLOW_IPS`` (or ``--forwarded-allow-ips``), never ``*`` if
clients can reach the app directly.
"""

# Settings are module variables named as Gunicorn's settings, in lower case

import os
from pathlib import Path

# Address of the app; the Helm chart's Service and the image's health check
# expect port 8080 (the app config's `server` settings apply to the
# development server only)
bind = ["0.0.0.0:8080"]

# One process with a few threads, as each worker loads the whole app
worker_class = "gthread"
workers = 1
threads = 4

# Workers whose main loop does not report to Gunicorn for `timeout` seconds
# (e.g., because it is blocked) are killed and restarted; with gthread
# workers, requests run in threads, so a slow or stuck request does not
# count. On shutdown (e.g., SIGTERM from Kubernetes), workers get
# `graceful_timeout` seconds to finish requests, i.e., less than the
# termination grace period of the pod
timeout = 30
graceful_timeout = 20
# Idle keep-alive connections are closed after this many seconds; longer than
# the keep-alive timeout of proxies in front of the app (60 seconds for
# ingress-nginx), so that a proxy does not reuse a connection just as it is
# closed (failing the request with `502`). Idle connections wait in the
# worker's poller, not in its threads (up to `worker_connections`, 1000)
keepalive = 75

# Size limits of the request line and headers (Gunicorn's defaults, set
# explicitly); larger requests are rejected (`400`). The size limit of request
# bodies is set by the app (`server.max_content_length`)
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Error log to stderr, as for the app's logs; no access log (enable it with
# `--access-logfile -`)
accesslog = None
errorlog = "-"

# No control socket (`gunicornc`), which would be created in the home
# directory, i.e., on the (possibly read-only) root file system
control_socket_disable = True


# `/dev/shm` is memory-backed, not a directory for temporary files
def _worker_tmp_dir(shm: Path = Path("/dev/shm")) -> str | None:  # noqa: S108
    """Get the directory for the heartbeat files of workers.

    The files are updated on each heartbeat, so a memory-backed directory
    (``/dev/shm``) is preferred; otherwise, the default temporary directory
    (``TMPDIR``, or ``/tmp``) is used.

    Args:
        shm: Memory-backed directory.

    Returns:
        ``shm`` if it is a writable directory, else ``None`` (Gunicorn's
        default).
    """
    if shm.is_dir() and os.access(shm, os.W_OK | os.X_OK):
        return str(shm)
    return None


worker_tmp_dir = _worker_tmp_dir()
