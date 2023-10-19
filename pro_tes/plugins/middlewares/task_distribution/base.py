"""Random task distribution middleware."""

from copy import deepcopy

import flask
from flask import current_app
from pydantic import HttpUrl  # pragma pylint: disable=no-name-in-module

from pro_tes.exceptions import MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware

# pragma pylint: disable=too-few-public-methods


class TaskDistributionBaseClass(AbstractMiddleware):
    """Task distribution middleware base class.

    Attributes:
        tes_urls: List of TES URIs.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.tes_urls: list[HttpUrl] = []

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Apply middleware to reque object.

        Args:
            request: Request object to be modified.

        Returns:
            Modified request object.

        Raises:
            MiddlewareException: If request has no JSON payload.
        """
        if request.json is None:
            raise MiddlewareException("Request has no JSON payload.")
        self._set_tes_urls(
            tes_urls=deepcopy(
                current_app.config.foca.tes["service_list"]  # type: ignore
            ),
            request=request,
        )
        request.json["tes_urls"] = self.tes_urls
        return request

    def _set_tes_urls(
        self,
        tes_urls: list[HttpUrl],
        request: flask.Request,  # pylint: disable=unused-argument
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_urls = list(set(tes_urls))
