"""Security checks on startup."""

import logging
import os

from foca import get_config

from pro_tes.config_models import Tes
from pro_tes.ga4gh.tes.backends import CLIENT_ID_ENV, CLIENT_SECRET_ENV
from pro_tes.utils.misc import get_custom_config

logger = logging.getLogger(__name__)


def _groups_of_role(mapping: dict[str, str], role: str) -> list[str]:
    """Get the groups mapped to a role.

    Args:
        mapping: Group mapping (`security.access_control.group_mapping`).
        role: Role.

    Returns:
        The groups mapped to the role, sorted.
    """
    return sorted(group for group, value in mapping.items() if value == role)


def check_role_groups() -> None:
    """Check that groups are mapped to the role `admin`, and log the mapping.

    Without such groups, nobody has the role `admin` (who may do
    everything), so this is an error unless
    `custom.access_control.require_admin` is false; so is a group mapping
    without the claim of the groups (`claim_groups`), which no group would
    match. A warning is logged if no group is mapped to the role `user`, as
    only admins can use the API then, and if access control is not enabled
    and enforced. Must be called in an app context.

    Raises:
        RuntimeError: Access control is enabled, no group is mapped to the
            role `admin` (or `claim_groups` is not set), and
            `custom.access_control.require_admin` is true.
    """
    conf = get_config().security.access_control
    if not (conf.enabled and conf.enforce):
        logger.warning(
            "Access control is not enforced, so anyone with a valid access "
            "token may do everything"
        )
        return
    mapping = conf.group_mapping or {}
    if mapping and conf.claim_groups is None:
        logger.warning(
            "`security.access_control.group_mapping` is set, but not "
            "`claim_groups`, so no group gets a role"
        )
        mapping = {}
    users = _groups_of_role(mapping, "user")
    if users:
        logger.info("Groups mapped to the role 'user': %s", ", ".join(users))
    else:
        logger.warning(
            "No group is mapped to the role 'user', so only admins can create, "
            "list, read and cancel tasks"
        )
    admins = _groups_of_role(mapping, "admin")
    if admins:
        logger.info("Groups mapped to the role 'admin': %s", ", ".join(admins))
    elif get_custom_config().access_control.require_admin:
        raise RuntimeError(
            "No group is mapped to the role 'admin': set "
            "`security.access_control.claim_groups` and `group_mapping` (e.g., "
            "to map an admin group of the identity provider to 'admin'), or "
            "set `custom.access_control.require_admin` to false to run "
            "without admins"
        )
    else:
        logger.warning("No group is mapped to the role 'admin'")


def _missing_env(*names: str) -> list[str]:
    """Get the environment variables that are not set (or empty).

    Args:
        *names: Names of environment variables.

    Returns:
        The names of the variables that are not set, or empty.
    """
    return [name for name in names if not os.environ.get(name)]


def check_backends(conf: Tes | None = None) -> None:
    """Check the credentials of the TES backends, and log how they are used.

    A warning is logged if no backend is configured, for each backend
    without token authentication (`auth: basic` or `none`), and if URLs
    with scheme `http` are allowed.

    Args:
        conf: TES backend configuration; by default, that of the current app
            (in an app context).

    Raises:
        RuntimeError: Environment variables with credentials are not set:
            the client ID and secret of proTES if a backend has `auth:
            client_credentials`, or the HTTP Basic credentials of a backend
            with `auth: basic`.
    """
    if conf is None:
        conf = get_custom_config().tes
    if not conf.service_list:
        logger.warning(
            "No TES backends are configured (`custom.tes.service_list`), so"
            " tasks can't be forwarded"
        )
    if "http" in conf.url_schemes:
        logger.warning(
            "URLs with scheme 'http' are allowed (`custom.tes.url_schemes`):"
            " credentials are sent unencrypted to such backends and identity"
            " providers; for local tests only"
        )
    missing: list[str] = []
    token_backends: list[str] = []
    for backend in conf.service_list:
        if backend.auth == "client_credentials":
            token_backends.append(backend.url)
        elif backend.auth == "basic":
            logger.warning(
                "TES backend %s: HTTP Basic credentials (`auth: basic`), a"
                " transitional mode for backends that don't accept tokens",
                backend.url,
            )
            if backend.basic is None:
                # prevented by validation, unless the config was changed later
                raise RuntimeError(
                    f"TES backend {backend.url}: `auth: basic` without `basic`"
                )
            missing.extend(
                _missing_env(backend.basic.username_env, backend.basic.password_env)
            )
        else:
            logger.warning(
                "TES backend %s: no authentication (`auth: none`)", backend.url
            )
    if token_backends:
        logger.info(
            "TES backends with access tokens of proTES: %s", ", ".join(token_backends)
        )
        missing.extend(_missing_env(CLIENT_ID_ENV, CLIENT_SECRET_ENV))
    if missing:
        raise RuntimeError(
            "Credentials of TES backends missing: set the environment variables"
            f" {', '.join(sorted(set(missing)))}"
        )
