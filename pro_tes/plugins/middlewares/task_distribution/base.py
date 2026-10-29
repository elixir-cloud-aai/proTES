"""Task distribution middleware base class."""

from typing import Any

from pro_tes.ga4gh.tes.backends import get_backends
from pro_tes.middleware.abstract_middleware import AbstractMiddleware


class TaskDistributionBaseClass(AbstractMiddleware):
    """Task distribution middleware base class.

    Attributes:
        tes_urls: List of TES URIs.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.tes_urls: list[str] = []

    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Add the TES backends to a task (`tes_urls`).

        Args:
            task: Task to be modified.

        Returns:
            The task, with the URLs of the backends that it is forwarded to,
            in order, as `tes_urls`.
        """
        self._set_tes_urls(
            tes_urls=[backend.url for backend in get_backends()],
            task=task,
        )
        task["tes_urls"] = self.tes_urls
        return task

    def _set_tes_urls(
        self,
        tes_urls: list[str],
        task: dict[str, Any],
    ) -> None:
        """Set TES URIs, once each, in the given order.

        The order is that of `custom.tes.backends`, with the backends of
        registries in their places (cf.
        :py:func:`pro_tes.ga4gh.tes.backends.get_backends`).

        Args:
            tes_urls: List of TES URIs.
            task: Task to be modified.
        """
        self.tes_urls = list(dict.fromkeys(tes_urls))
