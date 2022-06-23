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


class EngineProblem(InternalServerError):
    """The external workflow engine appears to experience problems."""
    pass


class EngineUnavailable(EngineProblem):
    """The external workflow engine is not available."""
    pass


class NoSuitableEngine(BadRequest):
    """Raised when the service does not know of a suitable engine to process
    the requested workflow run.
    """
    pass


class RunNotFound(NotFound):
    """Raised when workflow run with given run identifier was not found."""
    pass


class IdsUnavailableProblem(PyMongoError):
    """Raised when no unique run identifier could be found for insertion into
    the database collection.
    """
    pass


class StorageUnavailableProblem(OSError):
    """Raised when storage is not available for OS operations."""
    pass


exceptions = {
    Exception: {
        "message": "An unexpected error occurred.",
        "code": '500',
    },
    BadRequest: {
        "message": "The request is malformed.",
        "code": '400',
    },
    BadRequestProblem: {
        "message": "The request is malformed.",
        "code": '400',
    },
    ExtraParameterProblem: {
        "message": "The request is malformed.",
        "code": '400',
    },
    NoSuitableEngine: {
        "message": "No suitable workflow engine known.",
        "code": '400',
    },
    ValidationError: {
        "message": "The request is malformed.",
        "code": '400',
    },
    Unauthorized: {
        "message": " The request is unauthorized.",
        "code": '401',
    },
    Forbidden: {
        "message": "The requester is not authorized to perform this action.",
        "code": '403',
    },
    NotFound: {
        "message": "The requested resource wasn't found.",
        "code": '404',
    },
    RunNotFound: {
        "message": "The requested run wasn't found.",
        "code": '404',
    },
    EngineUnavailable: {
        "message": "Could not reach remote WES service.",
        "code": '500',
    },
    InternalServerError: {
        "message": "An unexpected error occurred.",
        "code": '500',
    },
    IdsUnavailableProblem: {
        "message": "No/few unique run identifiers available.",
        "code": '500',
    },
    StorageUnavailableProblem: {
        "message": "Storage is not accessible.",
        "code": '500',
    },
}
