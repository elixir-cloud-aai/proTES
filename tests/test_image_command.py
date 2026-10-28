"""Tests for the command of the image (Gunicorn with FOCA's settings)."""

import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any
from urllib.parse import urlsplit

from flask.testing import FlaskClient
from gunicorn.util import import_app
from pymongo import MongoClient
import pytest

from tests.gunicorn_settings import DOCKERFILE, image_command, load_settings

APP = "pro_tes.app:init_app()"


def test_image_command() -> None:
    """The image serves the app with Gunicorn and FOCA's settings.

    Without preloading the app, so that each worker creates it after
    forking (MongoDB clients must not be shared across forks). The command
    has no other options, so that FOCA's settings apply (e.g., the number
    of workers); deployments override them via `GUNICORN_CMD_ARGS`.
    """
    assert image_command() == [
        "gunicorn",
        "--config",
        "python:foca.gunicorn_config",
        APP,
    ]
    settings = load_settings()
    assert settings.app_uri == APP
    assert not settings.cfg.preload_app


def test_forwarded_allow_ips() -> None:
    """`X-Forwarded-*` headers are trusted from the local host only.

    That is, by default (Gunicorn's, unless `FORWARDED_ALLOW_IPS` is set);
    deployments behind a proxy set its addresses (here, via
    `--forwarded-allow-ips`).
    """
    assert "*" not in load_settings().cfg.forwarded_allow_ips
    cfg = load_settings("--forwarded-allow-ips 10.0.0.1").cfg
    assert cfg.forwarded_allow_ips == ["10.0.0.1"]


def test_cmd_args() -> None:
    """`GUNICORN_CMD_ARGS` overrides the settings."""
    args = shlex.join(["--workers", "2", "--threads", "8", "--timeout", "60"])
    cfg = load_settings(args).cfg
    assert (cfg.workers, cfg.threads, cfg.timeout) == (2, 8, 60)
    assert cfg.graceful_timeout == 20


def test_app_uri_loads(
    mongo_client: MongoClient[dict[str, Any]],
) -> None:
    """Gunicorn gets a WSGI app from the image's command."""
    app = import_app(load_settings().app_uri)
    assert callable(app)
    response = app.app.test_client().get("/ga4gh/tes/v1/service-info")
    assert response.status_code == 200


def _health_check_url() -> str:
    """Get the URL that the image's health check probes."""
    (url,) = re.findall(
        r"^ENV HEALTHCHECK_URL=(\S+)$",
        DOCKERFILE.read_text(encoding="utf-8"),
        re.M,
    )
    return str(url)


def test_health_check_port() -> None:
    """The image's health check probes the port Gunicorn listens on."""
    (bind,) = load_settings().cfg.bind
    assert urlsplit(_health_check_url()).port == int(bind.rpartition(":")[2])


def test_health_check_path(client: FlaskClient) -> None:
    """The image's health check probes the Swagger UI of the app."""
    response = client.get(urlsplit(_health_check_url()).path)
    assert response.status_code == 200


def test_load_settings_restores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loading the settings restores the arguments and the environment.

    That is, the arguments, `GUNICORN_CMD_ARGS` and the current directory.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setenv("GUNICORN_CMD_ARGS", "--workers 3")
    assert load_settings("--chdir /").cfg.workers == 1
    assert load_settings().cfg.workers == 1
    assert (os.getcwd(), sys.argv) == (str(tmp_path), ["pytest"])
    assert os.environ["GUNICORN_CMD_ARGS"] == "--workers 3"
    monkeypatch.delenv("GUNICORN_CMD_ARGS")
    load_settings("--workers 2")
    assert "GUNICORN_CMD_ARGS" not in os.environ
