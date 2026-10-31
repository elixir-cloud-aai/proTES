"""API server entry point."""

import logging
from pathlib import Path

from connexion import FlaskApp
from foca import Foca, get_collection
from foca.security.auth import require_no_service_clients

from pro_tes.gateway import GATEWAY, add_plugin_listing
from pro_tes.security import check_backends, check_task_ownership, check_user_groups
from pro_tes.utils.db import (
    TASK_COLLECTION,
    TASK_DB,
    check_unique_task_ids,
    drop_legacy_task_index,
    remove_stored_credentials,
)

logger = logging.getLogger(__name__)


def init_app() -> FlaskApp:
    """Initialize FOCA application.

    Checks that only owners can access tasks, refuses service clients (FOCA's
    `require_no_service_clients()`), as tasks are owned by users only, logs
    the groups of the role `user` and checks the credentials of the TES
    backends, removes credentials of callers from tasks stored by earlier
    versions, and drops the index of earlier versions on the tasks. FOCA
    validates the config, e.g., that groups are only mapped with a group
    claim, refuses to start without admins
    (`security.access_control.require_admin`), and warns of placeholders in
    the service info. FOCA loads the plugins of the config (`plugins`) and
    checks the backend list once, so that problems stop the app on startup.
    FOCA's plugin listing is served with the TES API (cf.
    :py:func:`pro_tes.gateway.add_plugin_listing`).

    Returns:
        FOCA application.

    Raises:
        RuntimeError: Access control is not enabled and enforced, service
            clients are configured, credentials of TES backends are missing,
            or stored tasks share identifiers (cf.
            :py:func:`pro_tes.utils.db.check_unique_task_ids`).
        pydantic.ValidationError: The config is invalid, e.g., access control
            is enabled without trusted token issuers, or issuers are trusted
            without audience.
        ValueError: The config is invalid (raised by FOCA), e.g., groups are
            mapped without a group claim, or there are no admins (unless
            admins are optional, `security.access_control.require_admin`),
            or a configured index conflicts with an existing index, or
            plugins can't be loaded, or the config has keys of earlier
            versions (e.g., `custom.middlewares`; with a hint).
    """
    foca = Foca(
        config_file=Path(__file__).resolve().parent / "config.yaml",
        custom_config_model="pro_tes.config_models.CustomConfig",
        gateway=GATEWAY,
    )
    add_plugin_listing(foca.conf)
    try:
        app = foca.create_app()
    except ValueError as exc:
        check_unique_task_ids(foca=foca, exc=exc)
        raise
    require_no_service_clients(app.app)
    with app.app.app_context():
        check_task_ownership()
        check_user_groups()
        check_backends()
        collection = get_collection(TASK_DB, TASK_COLLECTION)
        remove_stored_credentials(collection=collection)
        drop_legacy_task_index(collection=collection)
    return app


def run_app(app: FlaskApp) -> None:
    """Run FOCA application."""
    app.run(port=app.port)


if __name__ == "__main__":
    my_app = init_app()
    run_app(my_app)
