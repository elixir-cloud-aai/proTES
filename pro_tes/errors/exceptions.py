from connexion import ProblemException
from werkzeug.exceptions import (NotFound)


class TaskNotFound(ProblemException, NotFound, BaseException):
    """TaskNotFound(404) error compatible with Connexion."""

    def __init__(self, title=None, **kwargs):
        super(TaskNotFound, self).__init__(title=title, **kwargs)