"""Celery worker entry point."""

from pathlib import Path
from typing import Any

from celery import Celery
from celery.signals import worker_init
from foca import Foca

from pro_tes.config_models import CustomConfig
from pro_tes.gateway import GATEWAY
from pro_tes.security import check_backends
from pro_tes.utils.db import check_unique_task_ids

# The same gateway settings as the app's, so that both can be created in one
# process (cf. :py:mod:`foca.plugins.runtime`)
foca = Foca(
    config_file=Path(__file__).resolve().parent / "config.yaml",
    custom_config_model="pro_tes.config_models.CustomConfig",
    gateway=GATEWAY,
)


def create_celery_app(foca: Foca) -> Celery:
    """Create the Celery app, reporting stored tasks that share identifiers.

    Args:
        foca: FOCA instance.

    Returns:
        Celery app.

    Raises:
        RuntimeError: Stored tasks share identifiers (cf.
            :py:func:`pro_tes.utils.db.check_unique_task_ids`).
        ValueError: A configured index conflicts with an existing index.
    """
    try:
        return foca.create_celery_app()
    except ValueError as exc:
        check_unique_task_ids(foca=foca, exc=exc)
        raise


celery: Celery = create_celery_app(foca)
# Each worker process reserves one message at a time (tracking messages are
# acknowledged after their poll), so that messages wait in the broker, not
# in a busy worker; messages of scheduled polls are reserved until they are
# due anyway
celery.conf.worker_prefetch_multiplier = 1


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
        check_backends(conf=custom.tes, public_url=celery.conf.foca.server.public_url)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
