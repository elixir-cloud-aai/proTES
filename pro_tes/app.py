"""API server entry point."""

import logging
from pathlib import Path

from connexion import FlaskApp
from foca import Foca, get_collection, get_config
from pymongo.errors import DuplicateKeyError

from pro_tes.security import check_backends, check_role_groups, check_task_ownership
from pro_tes.utils.db import (
    drop_legacy_task_index,
    find_duplicate_task_ids,
    remove_stored_credentials,
)

logger = logging.getLogger(__name__)
# Database and collection of the tasks (`db.dbs` of `config.yaml`)
TASK_DB = "taskStore"
TASK_COLLECTION = "tasks"
# Number of shared task identifiers named when the app refuses to start
MAX_REPORTED_DUPLICATES = 10
# Placeholders of the service info in `config.yaml`, with reserved example
# domains (RFC 2606), which deployments replace
PLACEHOLDER_ID = "org.example.protes"
PLACEHOLDER_ORGANIZATION = {
    "name": "Example Organization",
    "url": "https://example.org",
}


def warn_placeholder_service_info() -> None:
    """Warn if the service info still has the placeholders of `config.yaml`.

    That is, if `id` is the placeholder, or the name or URL of `organization`
    is; the warning names the fields to set. Must be called in an app
    context.
    """
    service_info = get_config().service_info
    service = service_info.service if service_info is not None else {}
    fields = []
    if service.get("id") == PLACEHOLDER_ID:
        fields.append("`id`")
    organization = service.get("organization")
    if isinstance(organization, dict) and any(
        organization.get(key) == value
        for key, value in PLACEHOLDER_ORGANIZATION.items()
    ):
        fields.append("`organization`")
    if fields:
        logger.warning(
            "The service info (`service_info.service`) has placeholder"
            " values; set %s to describe the deployment (and `name`,"
            " `description`, `contactUrl` and `environment`)",
            ", ".join(fields),
        )


def check_unique_task_ids(foca: Foca, exc: ValueError) -> None:
    """Report stored tasks that share identifiers, if indexes failed.

    FOCA can't create the unique index on task identifiers (`task.id`, cf.
    `db` in `config.yaml`) while stored tasks share identifiers, e.g.,
    tasks stored by earlier versions, which didn't have the index. Nothing
    is changed: operators resolve the duplicates (cf. `docs/upgrading.md`).

    Args:
        foca: FOCA instance whose app could not be created.
        exc: Error of FOCA while creating the app.

    Raises:
        RuntimeError: The error is a duplicate key error, and stored tasks
            share identifiers; it names them (up to
            `MAX_REPORTED_DUPLICATES`), and the number of tasks for each.
    """
    if not isinstance(exc.__cause__, DuplicateKeyError):
        return
    dbs = foca.conf.db.dbs if foca.conf.db is not None else None
    db = (dbs or {}).get(TASK_DB)
    coll = ((db.collections if db is not None else None) or {}).get(TASK_COLLECTION)
    if coll is None or coll.client is None:
        return
    duplicates = find_duplicate_task_ids(collection=coll.client)
    if not duplicates:
        return
    named = ", ".join(
        f"{'(none)' if task_id is None else repr(task_id)}: {count}"
        for task_id, count in list(duplicates.items())[:MAX_REPORTED_DUPLICATES]
    )
    more = len(duplicates) - MAX_REPORTED_DUPLICATES
    raise RuntimeError(
        f"Stored tasks share {len(duplicates)} identifiers (tasks per"
        f" identifier: {named}{f', and {more} more' if more > 0 else ''}), so"
        " the unique index on task identifiers (`task.id`) can't be"
        " created; resolve the duplicates as described in the upgrading"
        " notes (Task identifiers), and start proTES again"
    ) from exc


def init_app() -> FlaskApp:
    """Initialize FOCA application.

    Checks that only owners can access tasks, the groups of the roles and
    the credentials of the TES backends, warns of placeholders in the
    service info, removes credentials of callers from tasks stored by
    earlier versions, and drops the index of earlier versions on the tasks.

    Returns:
        FOCA application.

    Raises:
        RuntimeError: Access control is not enabled and enforced, service
            clients are configured, no group is mapped to the role `admin`
            (unless admins are optional), credentials of TES backends
            are missing, or stored tasks share identifiers (cf.
            :py:func:`check_unique_task_ids`).
        pydantic.ValidationError: The config is invalid, e.g., access control
            is enabled without trusted token issuers, or issuers are trusted
            without audience.
        ValueError: A configured index conflicts with an existing index.
    """
    foca = Foca(
        config_file=Path(__file__).resolve().parent / "config.yaml",
        custom_config_model="pro_tes.config_models.CustomConfig",
    )
    try:
        app = foca.create_app()
    except ValueError as exc:
        check_unique_task_ids(foca=foca, exc=exc)
        raise
    with app.app.app_context():
        check_task_ownership()
        check_role_groups()
        check_backends()
        warn_placeholder_service_info()
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
