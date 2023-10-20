"""Celery worker entry point."""

from pathlib import Path

from celery import Celery
from foca import Foca  # type: ignore

foca = Foca(
    config_file=Path(__file__).resolve().parent / "config.yaml",
)
celery: Celery = foca.create_celery_app()
