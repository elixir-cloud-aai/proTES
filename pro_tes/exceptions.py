"""proTES exceptions.

Errors of the plugin engine of FOCA (cf. :py:mod:`foca.plugins.errors`) get
the problem details of the gateway error, with their problem type, from the
controller of `POST /tasks`; the mapping below is their fallback.
"""

from connexion.exceptions import (
    BadRequestProblem,
    ExtraParameterProblem,
    Forbidden,
    OAuthProblem,
    Unauthorized,
)
from foca.plugins import (
    ForwardTimeout,
    InvalidSelection,
    InvalidVia,
    LoopDetected,
    NoBackendAvailable,
    PipelineFailed,
)
from pydantic import ValidationError
from pymongo.errors import PyMongoError
import requests
from werkzeug.exceptions import (
    BadRequest,
    Conflict,
    InternalServerError,
    NotFound,
    RequestEntityTooLarge,
)


class BackendAuthError(RuntimeError):
    """Raised when proTES has no credentials for a TES backend.

    E.g., because its identity provider issued no access token.
    """


class BackendRequestNotSent(requests.ConnectionError):
    """Raised when a request to a discovered TES backend was certainly not sent.

    E.g., because connecting failed or timed out (cf.
    :py:class:`foca.utils.http.RequestNotSent`). A connection error, so that
    proTES handles it as it handles unreachable backends.
    """


class BackendAddressNotAllowed(BackendRequestNotSent):
    """Raised when the host of a discovered TES backend is not public.

    Or can't be resolved: FOCA's guarded HTTP client checks the address of
    each connection (cf. :py:class:`foca.utils.http.GuardedClient`). No
    request is sent.
    """


class BackendOutcomeUnknown(RuntimeError):
    """Raised when a TES backend may have processed a request, or not.

    E.g., because the response timed out, the connection was lost after the
    request was sent, or the backend answered with a server error. Such
    requests (to create or cancel tasks) are not sent again, neither to the
    backend nor to other backends, so that tasks are never created twice.

    Attributes:
        reason: Reason code, e.g., `read_timeout` or `http_502` (cf.
            :py:class:`foca.plugins.OutcomeUnknown`).
    """

    def __init__(self, message: str, reason: str = "outcome_unknown") -> None:
        """Create the error.

        Args:
            message: Description of the error.
            reason: Reason code.
        """
        super().__init__(message)
        self.reason = reason


class BackendNotConfigured(ValueError):
    """Raised when a TES backend is neither configured nor discovered."""


class IdsUnavailableProblem(PyMongoError):
    """Raised when task identifier is unavailable."""


class InlineContentTooLarge(RequestEntityTooLarge):
    """Raised when the inline contents of the inputs of a task are too large.

    Cf. `custom.controllers.post_task.max_inline_content`.
    """


class TaskNotFound(NotFound):
    """Raised when task with given task identifier was not found."""


class ProvenanceNotFound(NotFound):
    """Raised when a task has no provenance (e.g., created by an earlier version)."""


class TaskNotCancelable(Conflict):
    """Raised when a task has not finished, but can't be canceled.

    E.g., because it was not forwarded yet, so that its identifier at its
    TES instance is unknown.
    """


# problem details of exceptions (cf. `exceptions.mapping` in `config.yaml`)
exceptions = {
    Exception: {
        "title": "Unknown error",
        "detail": "An unexpected error occurred.",
        "status": 500,
    },
    BadRequest: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    BadRequestProblem: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    ExtraParameterProblem: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    ValidationError: {
        "title": "Bad request",
        "detail": "The request is malformed.",
        "status": 400,
    },
    Unauthorized: {
        "title": "Unauthorized",
        "detail": "The request is unauthorized.",
        "status": 401,
    },
    OAuthProblem: {
        "title": "Unauthorized",
        "detail": "The request is unauthorized.",
        "status": 401,
    },
    Forbidden: {
        "title": "Forbidden",
        "detail": "The requester is not authorized to perform this action.",
        "status": 403,
    },
    NotFound: {
        "title": "Not found",
        "detail": "The requested resource wasn't found.",
        "status": 404,
    },
    TaskNotFound: {
        "title": "Not found",
        "detail": "The requested task wasn't found.",
        "status": 404,
    },
    ProvenanceNotFound: {
        "title": "Not found",
        "detail": "No provenance was recorded for the task.",
        "status": 404,
    },
    TaskNotCancelable: {
        "title": "Conflict",
        "detail": (
            "The task can't be canceled: its identifier at its TES instance is unknown."
        ),
        "status": 409,
    },
    RequestEntityTooLarge: {
        "title": "Content Too Large",
        "detail": "The request body exceeds the size limit.",
        "status": 413,
    },
    InlineContentTooLarge: {
        "title": "Content Too Large",
        "detail": (
            "The inline content of an input of the task exceeds the size limit."
        ),
        "status": 413,
    },
    InternalServerError: {
        "title": "Internal server error",
        "detail": "An unexpected error occurred.",
        "status": 500,
    },
    IdsUnavailableProblem: {
        "title": "Internal server error",
        "detail": "No/few unique task identifiers available.",
        "status": 500,
    },
    BackendAuthError: {
        "title": "Internal server error",
        "detail": "No credentials for the TES instance of the task available.",
        "status": 500,
    },
    BackendNotConfigured: {
        "title": "Internal server error",
        "detail": "The TES instance of the task is not configured.",
        "status": 500,
    },
    BackendOutcomeUnknown: {
        "title": "Bad gateway",
        "detail": (
            "The TES instance did not confirm the request; it may have processed it."
        ),
        "status": 502,
    },
    # Errors of the plugin engine; `PipelineRejected` has the status of the
    # plugin's rejection, so it is not mapped
    InvalidSelection: {
        "title": "Bad request",
        "detail": "The pipeline or backends chosen in the tags are invalid.",
        "status": 400,
    },
    InvalidVia: {
        "title": "Bad request",
        "detail": "The Via header is too long.",
        "status": 400,
    },
    PipelineFailed: {
        "title": "Internal server error",
        "detail": "A step of the plugin pipeline failed.",
        "status": 500,
    },
    NoBackendAvailable: {
        "title": "Service unavailable",
        "detail": "No TES instance accepted the task.",
        "status": 503,
    },
    ForwardTimeout: {
        "title": "Service unavailable",
        "detail": "No TES instance accepted the task in time.",
        "status": 503,
    },
    LoopDetected: {
        "title": "Loop detected",
        "detail": "The request passed proTES before.",
        "status": 508,
    },
}
