"""API server entry point."""

import logging
from pathlib import Path

from connexion import FlaskApp
from foca import Foca, get_collection, get_config

from pro_tes.security import check_backends, check_role_groups
from pro_tes.utils.db import remove_stored_credentials

logger = logging.getLogger(__name__)
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


def init_app() -> FlaskApp:
    """Initialize FOCA application.

    Checks the groups of the roles and the credentials of the TES backends,
    warns of placeholders in the service info, and removes credentials of
    callers from tasks stored by earlier versions.

    Returns:
        FOCA application.

    Raises:
        RuntimeError: No group is mapped to the role `admin` (unless admins
            are optional), or credentials of TES backends are missing.
        pydantic.ValidationError: The config is invalid, e.g., access control
            is enabled without trusted token issuers, or issuers are trusted
            without audience.
    """
    foca = Foca(
        config_file=Path(__file__).resolve().parent / "config.yaml",
        custom_config_model="pro_tes.config_models.CustomConfig",
    )
    app = foca.create_app()
    with app.app.app_context():
        check_role_groups()
        check_backends()
        warn_placeholder_service_info()
        remove_stored_credentials(collection=get_collection("taskStore", "tasks"))
    return app


def run_app(app: FlaskApp) -> None:
    """Run FOCA application."""
    app.run(port=app.port)


if __name__ == "__main__":
    my_app = init_app()
    run_app(my_app)
