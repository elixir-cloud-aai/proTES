"""Security checks on startup."""

import logging

from foca import get_config

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
