"""Random task distribution middleware."""

import flask

from pro_tes.exceptions import MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware
from pro_tes.utils.misc import get_custom_config


class TaskDistributionBaseClass(AbstractMiddleware):
    """Task distribution middleware base class.

    Attributes:
        tes_urls: List of TES URIs.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.tes_urls: list[str] = []

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
            tes_urls=[backend.url for backend in get_custom_config().tes.service_list],
            request=request,
        )
        request.json["tes_urls"] = self.tes_urls
        return request

    def _set_tes_urls(
        self,
        tes_urls: list[str],
        request: flask.Request,
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_urls = list(set(tes_urls))
