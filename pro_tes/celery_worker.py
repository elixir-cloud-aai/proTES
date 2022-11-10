"""Entry point for Celery workers."""

from pathlib import Path

from foca import Foca

foca = Foca(Path(__file__).resolve().parent / "config.yaml")
celery = foca.create_celery_app()
