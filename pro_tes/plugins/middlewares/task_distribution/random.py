"""Random task distribution middleware."""

import random
from typing import Any

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
        task: dict[str, Any],
    ) -> None:
        """Set TES URIs.

        Args:
            tes_urls: List of TES URIs.
            task: Task to be modified.
        """
        self.tes_urls = list(set(tes_urls))
        random.shuffle(self.tes_urls)
