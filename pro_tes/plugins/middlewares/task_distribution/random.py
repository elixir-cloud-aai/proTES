"""Random task distribution middleware."""

import random

import flask

from pydantic import HttpUrl  # pragma pylint: disable=no-name-in-module

from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)

# pragma pylint: disable=too-few-public-methods


class TaskDistributionRandom(TaskDistributionBaseClass):
    """Random task distribution middleware.

    Randomly huffles the available TES instances.
    """

    def _set_tes_urls(
        self,
        tes_urls: list[HttpUrl],
        request: flask.Request,
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            request: Request object to be modified.
        """
        self.tes_urls = list(set(tes_urls))
        random.shuffle(self.tes_urls)
