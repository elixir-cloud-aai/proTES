"""proTES exceptions."""

from connexion.exceptions import (
    BadRequestProblem,
    ExtraParameterProblem,
    Forbidden,
    OAuthProblem,
    Unauthorized,
)
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from werkzeug.exceptions import (
    BadRequest,
    InternalServerError,
    NotFound,
    RequestEntityTooLarge,
)

# pylint: disable="too-few-public-methods"


class IdsUnavailableProblem(PyMongoError):
    """Raised when task identifier is unavailable."""


class InputUriError(ValueError):
    """Raised when input URI cannot be parsed."""


class IPDistanceCalculationError(ValueError):
    """Raised when IP distance cannot be calculated."""


class MiddlewareException(ValueError):
    """Raised when a middleware could not be applied."""


class InvalidMiddleware(MiddlewareException):
    """Raised when a middleware is invalid."""


class NoTesInstancesAvailable(ValueError):
    """Raised when no TES instances are available."""


class TaskNotFound(NotFound):
    """Raised when task with given task identifier was not found."""


class TesUriError(ValueError):
    """Raised when TES URI cannot be parsed."""


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
    TesUriError: {
        "title": "Bad request",
        "detail": "TES URI cannot be parsed.",
        "status": 400,
    },
    InputUriError: {
        "title": "Bad request",
        "detail": "Input URI cannot be parsed.",
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
    RequestEntityTooLarge: {
        "title": "Content Too Large",
        "detail": "The request body exceeds the size limit.",
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
    NoTesInstancesAvailable: {
        "title": "Internal server error",
        "detail": "No valid TES instances available.",
        "status": 500,
    },
    MiddlewareException: {
        "title": "Internal server error",
        "detail": "Middleware could not be applied.",
        "status": 500,
    },
    InvalidMiddleware: {
        "title": "Internal server error",
        "detail": "Middleware is invalid.",
        "status": 500,
    },
    IPDistanceCalculationError: {
        "title": "Internal server error",
        "detail": "IP distance calculation failed.",
        "status": 500,
    },
}
