"""Abstract class for all the middlewares."""

import abc

import flask

# pragma pylint: disable=too-few-public-methods


class AbstractMiddleware(metaclass=abc.ABCMeta):
    """Abstract class for middlewares."""

    @abc.abstractmethod
    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Modify request object.

        Args:
            request: Request object to be modified.

        Returns:
            Modified request object.
        """
