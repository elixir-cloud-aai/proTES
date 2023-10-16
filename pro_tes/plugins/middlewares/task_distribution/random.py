"""Random task distribution middleware."""

import flask
import random

from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)

# pragma pylint: disable=too-few-public-methods


class TaskDistributionRandom(TaskDistributionBaseClass):
    """Random task distribution middleware.

    Randomly huffles the available TES instances.
    """

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
        random.shuffle(self.tes_uris)
