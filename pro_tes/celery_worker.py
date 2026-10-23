"""Celery worker entry point."""

from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import worker_init
from foca import Foca

from pro_tes.config_models import CustomConfig
from pro_tes.security import check_backends

foca = Foca(
    config_file=Path(__file__).resolve().parent / "config.yaml",
    custom_config_model="pro_tes.config_models.CustomConfig",
)
celery: Celery = foca.create_celery_app()


@worker_init.connect
def check_worker_backends(**kwargs: Any) -> None:
    """Check the credentials of the TES backends when the worker starts.

    As the app does (cf. :py:func:`pro_tes.security.check_backends`), so
    that a worker without them doesn't start, instead of failing to track
    tasks.

    Args:
        **kwargs: Arguments of the signal; ignored.

    Raises:
        SystemExit: Credentials of TES backends are missing (Celery logs
            other exceptions of signal handlers, and starts anyway).
    """
    custom = (celery.conf.foca.model_extra or {}).get("custom")
    if not isinstance(custom, CustomConfig):
        raise SystemExit("Celery worker without the custom configuration of proTES")
    try:
        check_backends(conf=custom.tes)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
