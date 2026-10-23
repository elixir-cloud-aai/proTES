"""Owners of tasks.

The user creating a task (the subject of its access token, cf.
:py:func:`foca.security.access_control.get_current_subject`) becomes its
owner, and only owners may read and cancel it (enforced by the policies, cf.
`security.access_control` in `config.yaml`); listings only show the tasks of
the caller. Tasks are not shared, and admins don't see the tasks of others.

Ownership is stored as resource-scoped roles of FOCA (`g2` rules) on the
request paths of a task, so that FOCA enforces it: the task itself (e.g.,
`/ga4gh/tes/v1/tasks/ABC123`) and its cancellation
(`/ga4gh/tes/v1/tasks/ABC123:cancel`), which is not a sub-path of the task,
so that roles on the task don't apply to it.
"""

from foca.security.access_control import (
    add_owner,
    get_current_subject,
    list_roles,
    list_subject_roles,
)
from foca.security.access_control.constants import OWNER_ROLE

from pro_tes.exceptions import Unauthorized

# Suffix of the path of the cancellation of a task (`/tasks/{id}:cancel`)
CANCEL_SUFFIX = ":cancel"


def get_owner() -> str:
    """Get the owner of a task created by the current request.

    Returns:
        The user of the current request (cf.
        :py:func:`foca.security.access_control.get_current_subject`).

    Raises:
        pro_tes.exceptions.Unauthorized: The request has no identified user,
            e.g., because its token lacks the identity claim.
    """
    owner = get_current_subject()
    if owner is None:
        raise Unauthorized("User could not be identified")
    return owner


def task_resources(collection: str, task_id: str) -> list[str]:
    """Get the resources (request paths) owners of a task have a role on.

    Args:
        collection: Request path of the tasks (e.g., `/ga4gh/tes/v1/tasks`).
        task_id: Identifier of the task.

    Returns:
        The path of the task and of its cancellation.
    """
    task = f"{collection.rstrip('/')}/{task_id}"
    return [task, f"{task}{CANCEL_SUFFIX}"]


def task_has_roles(collection: str, task_id: str) -> bool:
    """Check whether roles are granted on a task (or its cancellation).

    Roles may be left by deleted tasks, or granted to a task being created
    concurrently; their identifiers must not be used for new tasks, so
    that their owners don't get access to them.

    Args:
        collection: Request path of the tasks (e.g., `/ga4gh/tes/v1/tasks`).
        task_id: Identifier of the task.

    Returns:
        Whether any subject has a role on the task or its cancellation.
    """
    return any(
        list_roles(resource=resource)
        for resource in task_resources(collection=collection, task_id=task_id)
    )


def add_task_owner(collection: str, task_id: str, owner: str) -> None:
    """Make a user the owner of a task.

    Args:
        collection: Request path of the tasks (e.g., `/ga4gh/tes/v1/tasks`).
        task_id: Identifier of the task.
        owner: User identifier, without prefix (cf. :py:func:`get_owner`).
    """
    for resource in task_resources(collection=collection, task_id=task_id):
        add_owner(resource=resource, subject=owner)


def owned_tasks_filter(collection: str) -> dict[str, dict[str, list[str]]]:
    """Get a MongoDB filter for the tasks of the current user.

    Unlike :py:func:`foca.security.access_control.resource_filter`, the
    filter is not empty for admins: they only see their own tasks, too.

    Args:
        collection: Request path of the tasks (e.g., `/ga4gh/tes/v1/tasks`).

    Returns:
        A filter for the stored tasks (`task.id`) that the current user owns,
        in the order of their identifiers.
    """
    ids = {
        role.id
        for role in list_subject_roles(prefix=collection)
        if role.role == OWNER_ROLE and not role.id.endswith(CANCEL_SUFFIX)
    }
    return {"task.id": {"$in": sorted(ids)}}
