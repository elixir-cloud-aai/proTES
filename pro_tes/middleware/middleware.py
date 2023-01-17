"""Middleware to inject into TES requests."""

import abc
from typing import (
    Dict,
)

from pro_tes.middleware.task_distribution import task_distribution_by_distance, \
    random_task_distribution


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
        self.tes_uri = []
        self.input_uri = []

    def modify_request(self, request) -> Dict:
        """Add the best possible TES instance to request body."""

        if "inputs" in request.json.keys():
            for index in range(len(request.json["inputs"])):
                if "url" in request.json["inputs"][index].keys():
                    self.input_uri.append(request.json["inputs"][index]["url"])
                else:
                    continue

        if len(self.input_uri) is not 0:
            self.tes_uri = task_distribution_by_distance(self.input_uri)
        else:
            self.tes_uri = random_task_distribution()

        if len(self.tes_uri) != 0:
            request.json["tes_uri"] = self.tes_uri
        else:
            raise Exception
        return request
