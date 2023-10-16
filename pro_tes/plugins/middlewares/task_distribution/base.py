"""Random task distribution middleware."""

from copy import deepcopy

import flask
from flask import current_app

from pro_tes.exceptions import MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware

# pragma pylint: disable=too-few-public-methods


class TaskDistributionBaseClass(AbstractMiddleware):
    """Task distribution middleware base class.

    Attributes:
        tes_uris: List of TES URIs.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.tes_uris: list[str] = []

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
        self._set_tes_uris(
            tes_uris=deepcopy(
                current_app.config.foca.tes["service_list"]  # type: ignore
            ),
            request=request,
        )
        request.json["tes_uris"] = self.tes_uris
        return request

    def _set_tes_uris(
        self,
        tes_uris: list[str],
        request: flask.Request,
    ) -> None:
        """Set TES URIs.

        Args:
            tes_uris: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_uris = tes_uris
