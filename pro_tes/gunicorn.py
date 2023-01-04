"""Gunicorn entry point."""

import os

from foca.models.config import Config

from pro_tes.app import init_app

# Source application configuration
app_config: Config = init_app().app.config.foca

# Set Gunicorn number of workers and threads
workers = int(os.environ.get("GUNICORN_PROCESSES", "1"))
threads = int(os.environ.get("GUNICORN_THREADS", "1"))

# Set allowed IPs
forwarded_allow_ips = "*"  # pylint: disable=invalid-name

# Set Gunicorn bind address
bind = f"{app_config.server.host}:{app_config.server.port}"

# Source environment variables for Gunicorn workers
raw_env = [
    f'TES_CONFIG={os.environ.get("TES_CONFIG", "")}',
    f'RABBIT_HOST={os.environ.get("RABBIT_HOST", app_config.jobs.host)}',
    f'RABBIT_PORT={os.environ.get("RABBIT_PORT", app_config.jobs.port)}',
    f'MONGO_HOST={os.environ.get("MONGO_HOST", app_config.db.host)}',
    f'MONGO_PORT={os.environ.get("MONGO_PORT", app_config.db.port)}',
    f'MONGO_DBNAME={os.environ.get("MONGO_DBNAME", "taskStore")}',
    f'MONGO_USERNAME={os.environ.get("MONGO_USERNAME", "")}',
    f'MONGO_PASSWORD={os.environ.get("MONGO_PASSWORD", "")}',
]
