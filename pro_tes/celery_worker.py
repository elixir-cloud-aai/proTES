"""Celery worker entry point."""

from pathlib import Path

from celery import Celery
from foca import Foca

foca = Foca(
    config_file=Path(__file__).resolve().parent / "config.yaml",
    custom_config_model="pro_tes.config_models.CustomConfig",
)
celery: Celery = foca.create_celery_app()
