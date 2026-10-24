"""Abstract class for all the middlewares."""

import abc
from typing import Any


class AbstractMiddleware(metaclass=abc.ABCMeta):
    """Abstract class for middlewares.

    Middlewares get the task of a request to create one (`POST /tasks`), not
    the request itself, so that they don't depend on the web framework.
    """

    @abc.abstractmethod
    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Modify a task.

        Args:
            task: Task to be modified (a TES task, as a JSON object): a copy
                for this middleware, which it may change in place.

        Returns:
            The modified task, e.g., `task` itself.
        """
