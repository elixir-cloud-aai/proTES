"""Security checks on startup."""

import logging
import os

from foca import get_config, get_custom_config

from pro_tes.config_models import CustomConfig, Tes
from pro_tes.ga4gh.tes.backends import CLIENT_ID_ENV, CLIENT_SECRET_ENV

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


def check_task_ownership() -> None:
    """Check that only the owners of tasks can access them.

    Owners are enforced by access control (cf.
    :py:mod:`pro_tes.ga4gh.tes.ownership`), so it must be enabled and
    enforced. Tasks are owned by the users of the access tokens only, so
    service clients are refused too, by FOCA's
    :py:func:`foca.security.auth.require_no_service_clients`, which the app
    calls on startup. Must be called in an app context.

    Raises:
        RuntimeError: Access control is not enabled and enforced.
    """
    conf = get_config().security.access_control
    if not (conf.enabled and conf.enforce):
        raise RuntimeError(
            "Access control must be enabled and enforced "
            "(`security.access_control.enabled` and `enforce`), so that only "
            "the owners of tasks can access them"
        )


def check_user_groups() -> None:
    """Log the groups mapped to the role `user`, or warn if there are none.

    Nobody can create or list tasks without such groups. FOCA checks the
    groups of the role `admin` (`security.access_control.require_admin`) and
    that groups are only mapped with a group claim (`claim_groups`). Must be
    called in an app context.
    """
    mapping = get_config().security.access_control.group_mapping or {}
    users = _groups_of_role(mapping, "user")
    if users:
        logger.info("Groups mapped to the role 'user': %s", ", ".join(users))
    else:
        logger.warning(
            "No group is mapped to the role 'user', so nobody can create or list tasks"
        )


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

    A warning is logged if no backend is configured and discovery is off,
    for each backend without token authentication (`auth: basic` or `none`),
    if discovered backends get no tokens or may have private addresses, and
    if URLs with scheme `http` are allowed.

    Args:
        conf: TES backend configuration; by default, that of the current app
            (in an app context).

    Raises:
        RuntimeError: Environment variables with credentials are not set:
            the client ID and secret of proTES if a backend has (or
            discovered backends get) `auth: client_credentials`, or the HTTP
            Basic credentials of a backend with `auth: basic`.
    """
    if conf is None:
        conf = get_custom_config(CustomConfig).tes
    discovery = conf.discovery
    if discovery.enabled:
        logger.info(
            "Discovering TES backends at registry %s every %d seconds"
            " (`custom.tes.discovery`), with `auth: %s`",
            discovery.registry_url,
            discovery.refresh_interval,
            discovery.auth,
        )
        if discovery.auth == "none":
            logger.warning("Discovered TES backends get no credentials (`auth: none`)")
        if discovery.allow_private_addresses:
            logger.warning(
                "Discovered TES backends may have private addresses"
                " (`custom.tes.discovery.allow_private_addresses`); for local"
                " setups only"
            )
    elif not conf.service_list:
        logger.warning(
            "No TES backends are configured (`custom.tes.service_list`) or"
            " discovered (`custom.tes.discovery`), so tasks can't be forwarded"
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
        # logs the URLs of the backends, not tokens
        # nosemgrep  # logger-credential-disclosure: see above
        logger.info(
            "TES backends with access tokens of proTES: %s", ", ".join(token_backends)
        )
    if token_backends or discovery.uses_tokens:
        missing.extend(_missing_env(CLIENT_ID_ENV, CLIENT_SECRET_ENV))
    if missing:
        raise RuntimeError(
            "Credentials of TES backends missing: set the environment variables"
            f" {', '.join(sorted(set(missing)))}"
        )
