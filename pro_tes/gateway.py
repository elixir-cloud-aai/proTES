"""proTES as a gateway: the plugin engine of FOCA processes new tasks.

proTES declares itself a gateway of the TES API (:py:data:`GATEWAY`, passed
to `Foca(gateway=...)` by the app and the Celery worker), so that FOCA runs
the plugin pipeline of `plugins` (cf. :py:mod:`foca.plugins`) for each new
task: plugins may change the task (phase `request`), choose and order the
TES backends (`route`), and change the request to each backend (`forward`)
and the response to the client (`response`).

The functions of this module are the app's callbacks of the engine; they get
plain data, without the web framework:

- :py:func:`sanitize_task` removes credentials from the URLs of inputs and
  outputs, and checks the size of their inline contents, after each plugin
  that changed the task;
- :py:func:`validate_task` checks the task against the TES models of proTES
  and of py-tes, after each such plugin (invalid output counts as a failed
  plugin);
- :py:func:`check_outbound` checks that the task of an outbound request can
  be relayed, after the `forward` phase of each attempt.
"""

from collections.abc import Mapping
from copy import deepcopy
from importlib.metadata import version
from typing import Any, Literal

from foca import get_custom_config
from foca.plugins import OutboundRequest
from foca.plugins.backends import BackendListConfig
from foca.plugins.runtime import GatewayApp
from foca.plugins.tags import strip_for_forwarding
import tes

from pro_tes.config_models import CustomConfig
from pro_tes.exceptions import BadRequest, InlineContentTooLarge
from pro_tes.ga4gh.tes.models import TesTask
from pro_tes.utils.misc import has_credentials, strip_auth

TASKS_PATH = "/ga4gh/tes/v1/tasks"
"""Path of the requests to create tasks, below the URL of a backend."""

RESERVED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
"""Headers (lower case) that plugins can't set in requests to backends: they
would change where or how requests are sent, e.g., to another virtual host
with the backend's credentials."""

# Fields of tasks that TES servers set (TES servers may reject tasks that set
# them, as proTES does); never relayed
SERVER_FIELDS = ("creation_time", "logs")


def handle_url_credentials(
    payload: dict[str, Any], mode: Literal["reject", "strip"]
) -> None:
    """Reject or remove credentials in the URLs of inputs and outputs.

    Credentials are user names and passwords (user information, e.g.,
    `https://user:password@host/path`). Inputs and outputs are kept
    otherwise, also inputs without URL (with `content`); malformed ones
    are left to the validation of the task.

    Args:
        payload: Task (JSON); changed in place.
        mode: Whether tasks with credentials are rejected (`reject`), or
            the credentials are removed (`strip`).

    Raises:
        pro_tes.exceptions.BadRequest: A URL has credentials, and `mode`
            is `reject`; or a URL is malformed.
    """
    for field in ("inputs", "outputs"):
        items = payload.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            url = item.get("url") if isinstance(item, dict) else None
            if not isinstance(url, str):
                continue
            try:
                found = has_credentials(url)
            except ValueError as exc:
                raise BadRequest(f"Malformed URL in `{field}`") from exc
            if not found:
                continue
            if mode == "reject":
                raise BadRequest(
                    f"URLs in `{field}` must not have credentials (user"
                    " names or passwords)"
                )
            item["url"] = strip_auth(url)


def check_inline_content(payload: dict[str, Any], limit: int) -> None:
    """Check the size of the inline contents of the inputs of a task.

    The limit applies to the content of each input separately (the TES
    specification recommends a minimum size per field); all contents
    together are limited by the size of request bodies.

    Args:
        payload: Task (JSON).
        limit: Maximum size of the content of an input
            (`inputs[].content`), in bytes (UTF-8).

    Raises:
        pro_tes.exceptions.InlineContentTooLarge: A content is larger.
    """
    inputs = payload.get("inputs")
    if not isinstance(inputs, list):
        return
    size = max(
        (
            len(item["content"].encode("utf-8", errors="surrogatepass"))
            for item in inputs
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ),
        default=0,
    )
    if size > limit:
        raise InlineContentTooLarge(
            f"Inline content of an input (`inputs[].content`) exceeds"
            f" {limit} bytes: {size}"
        )


def sanitize_task(payload: Any) -> Any:
    """Sanitize a task after a plugin changed it.

    Credentials in the URLs of inputs and outputs are removed (whatever
    `custom.controllers.post_task.url_credentials` says, which applies to the
    task of the client), and the inline contents of the inputs are checked.

    Args:
        payload: Task (JSON); changed in place.

    Returns:
        The task.

    Raises:
        pro_tes.exceptions.BadRequest: A URL is malformed.
        pro_tes.exceptions.InlineContentTooLarge: An inline content is
            larger than `custom.controllers.post_task.max_inline_content`.
    """
    if isinstance(payload, dict):
        handle_url_credentials(payload=payload, mode="strip")
        limit = get_custom_config(CustomConfig).controllers.post_task.max_inline_content
        check_inline_content(payload=payload, limit=limit)
    return payload


def tes_task(payload: Any) -> tes.Task:
    """Convert a task to the model of py-tes, as it is relayed.

    Args:
        payload: Task (JSON); not changed.

    Returns:
        The task, without the fields that TES servers set
        (`SERVER_FIELDS`).

    Raises:
        TypeError: The task is not a JSON object, or has fields or values
            that py-tes doesn't accept.
        ValueError: The task has values that py-tes doesn't accept.
    """
    if not isinstance(payload, dict):
        raise TypeError("Tasks are JSON objects")
    task = {
        key: deepcopy(value)
        for key, value in payload.items()
        if key not in SERVER_FIELDS
    }
    if task.get("inputs") is not None:
        task["inputs"] = [tes.models.Input(**item) for item in task["inputs"]]
    if task.get("outputs") is not None:
        task["outputs"] = [tes.models.Output(**item) for item in task["outputs"]]
    if task.get("resources") is not None:
        task["resources"] = tes.models.Resources(**task["resources"])
    if task.get("executors") is not None:
        task["executors"] = [tes.models.Executor(**item) for item in task["executors"]]
    return tes.Task(**task)


def validate_task(payload: Any) -> None:
    """Validate a task, e.g., after a plugin changed it.

    Args:
        payload: Task (JSON).

    Raises:
        pydantic.ValidationError: The task is invalid as per the TES model
            of proTES.
        TypeError: The task can't be relayed (cf. :py:func:`tes_task`).
        ValueError: The task can't be relayed.
    """
    if not isinstance(payload, dict):
        raise TypeError("Tasks are JSON objects")
    TesTask(**payload)
    tes_task(payload)


def check_outbound(outbound: OutboundRequest) -> None:
    """Check that the task of an outbound request can be relayed.

    Requests that fail the check are not sent; the next backend is tried.
    Plugins can't change the method and path of the request: tasks are
    created with `POST /ga4gh/tes/v1/tasks` (`TASKS_PATH`), so that other
    requests (e.g., for tasks of other users) can't be sent with proTES's
    credentials.

    Args:
        outbound: Outbound request, after the `forward` phase.

    Raises:
        pro_tes.exceptions.BadRequest: A URL is malformed.
        pro_tes.exceptions.InlineContentTooLarge: An inline content is
            larger than `custom.controllers.post_task.max_inline_content`.
        TypeError: The task can't be relayed (cf. :py:func:`tes_task`).
        ValueError: The task can't be relayed, the method or path of the
            request was changed, its query is not a mapping of names to
            lists of values, or it has reserved headers
            (`RESERVED_HEADERS`).
    """
    if outbound.method != "POST" or outbound.path != TASKS_PATH:
        raise ValueError(
            f"Tasks are created with POST {TASKS_PATH}, not"
            f" {outbound.method} {outbound.path}"
        )
    query = outbound.query
    if not isinstance(query, dict) or not all(
        isinstance(name, str)
        and isinstance(values, list)
        and all(isinstance(value, str) for value in values)
        for name, values in query.items()
    ):
        raise ValueError("Query parameters must map names to lists of strings")
    reserved = sorted(
        name for name in outbound.headers if name.lower() in RESERVED_HEADERS
    )
    if reserved:
        raise ValueError(f"Headers reserved for proTES: {', '.join(reserved)}")
    # dry run on a copy: `relayed_task` strips the credentials of the sent task
    sanitize_task(deepcopy(outbound.payload))
    tes_task(outbound.payload)


def relayed_task(payload: Any, prefix: str) -> tes.Task:
    """Get the task of an outbound request as it is relayed to a backend.

    Args:
        payload: Task of the outbound request; not changed.
        prefix: Prefix of the tags reserved for the gateway
            (`plugins.tags.prefix`).

    Returns:
        The task, without the fields that TES servers set, without tags
        under the prefix (cf. :py:func:`foca.plugins.tags.strip_for_forwarding`)
        and without credentials in the URLs of inputs and outputs (also
        those that plugins of the `forward` phase added).

    Raises:
        pro_tes.exceptions.BadRequest: A URL is malformed.
        TypeError: The task can't be relayed (cf. :py:func:`tes_task`).
        ValueError: The task can't be relayed.
    """
    payload = deepcopy(payload)
    if isinstance(payload, dict):
        handle_url_credentials(payload=payload, mode="strip")
    task = tes_task(payload)
    if isinstance(task.tags, Mapping):
        task.tags = strip_for_forwarding(task.tags, prefix)
    return task


def backend_entries(config: Any) -> BackendListConfig:
    """Get the entries of the backend list of proTES.

    Args:
        config: FOCA configuration of proTES
            (:py:class:`foca.models.config.Config`).

    Returns:
        The entries of `custom.tes.backends`.
    """
    backends: BackendListConfig = config.custom.tes.backends
    return backends


GATEWAY = GatewayApp(
    api="tes",
    validate=validate_task,
    sanitize=sanitize_task,
    validate_outbound=check_outbound,
    name="proTES",
    version=version("pro-tes"),
    backends=backend_entries,
)
"""Settings of proTES as a gateway (cf. :py:class:`foca.plugins.runtime.GatewayApp`)."""
