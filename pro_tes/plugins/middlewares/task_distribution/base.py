"""Task distribution middleware base class."""

import flask

from pro_tes.exceptions import MiddlewareException
from pro_tes.ga4gh.tes.discovery import get_backends
from pro_tes.middleware.abstract_middleware import AbstractMiddleware


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
            tes_urls=[backend.url for backend in get_backends()],
            request=request,
        )
        request.json["tes_urls"] = self.tes_urls
        return request

    def _set_tes_urls(
        self,
        tes_urls: list[str],
        request: flask.Request,
    ) -> None:
        """Set TES URIs, once each, in the given order.

        The order is that of `custom.tes.service_list`, followed by the
        discovered backends (cf. :py:func:`pro_tes.ga4gh.tes.discovery.get_backends`).

        Args:
            tes_urls: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_urls = list(dict.fromkeys(tes_urls))
