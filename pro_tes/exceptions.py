"""proTES exceptions."""

from connexion.exceptions import (
    BadRequestProblem,
    ExtraParameterProblem,
    Forbidden,
    Unauthorized,
)
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from werkzeug.exceptions import (
    BadRequest,
    InternalServerError,
    NotFound,
)


class TaskNotFound(NotFound):
    """Raised when task with given task identifier was not found."""


class IdsUnavailableProblem(PyMongoError):
    """Raised when task identifier is unavailable."""


exceptions = {
    Exception: {
        "message": "An unexpected error occurred.",
        "code": "500",
    },
    BadRequest: {
        "message": "The request is malformed.",
        "code": "400",
    },
    BadRequestProblem: {
        "message": "The request is malformed.",
        "code": "400",
    },
    ExtraParameterProblem: {
        "message": "The request is malformed.",
        "code": "400",
    },
    ValidationError: {
        "message": "The request is malformed.",
        "code": "400",
    },
    Unauthorized: {
        "message": " The request is unauthorized.",
        "code": "401",
    },
    Forbidden: {
        "message": "The requester is not authorized to perform this action.",
        "code": "403",
    },
    NotFound: {
        "message": "The requested resource wasn't found.",
        "code": "404",
    },
    TaskNotFound: {
        "message": "The requested task wasn't found.",
        "code": "404",
    },
    InternalServerError: {
        "message": "An unexpected error occurred.",
        "code": "500",
    },
    IdsUnavailableProblem: {
        "message": "No/few unique task identifiers available.",
        "code": "500",
    },
}
