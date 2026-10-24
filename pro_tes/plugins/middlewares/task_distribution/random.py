"""Random task distribution middleware."""

import random

import flask

from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)


class TaskDistributionRandom(TaskDistributionBaseClass):
    """Random task distribution middleware.

    Randomly huffles the available TES instances.
    """

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
        self.tes_urls = list(dict.fromkeys(tes_urls))
        random.shuffle(self.tes_urls)
