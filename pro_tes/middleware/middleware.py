"""Middleware to inject into TES requests."""

import abc
from typing import (
    Dict,
)

from pro_tes.middleware.task_distribution import task_distribution

# pragma pylint: disable=too-few-public-methods


class AbstractMiddleware(metaclass=abc.ABCMeta):
    """Abstract class to implement different middleware."""

    @abc.abstractmethod
    def modify_request(self, request):
        """Modify the request before it is sent to the TES instance."""


class TaskDistributionMiddleware(AbstractMiddleware):
    """Inject task distribution logic.

    Attributes:
        tes_uri: TES instance best suited for TES task.
    """

    def __init__(self):
        """Construct object instance."""
        self.tes_uri = task_distribution()

    def modify_request(self, request) -> Dict:
        """Add TES instance to request body."""
        if len(self.tes_uri) != 0:
            request.json["tes_uri"] = self.tes_uri
        else:
            raise Exception
        return request
