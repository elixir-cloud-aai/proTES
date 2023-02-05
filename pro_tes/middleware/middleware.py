"""Middleware to inject into TES requests."""

import abc
from datetime import datetime
from typing import List

from pro_tes.middleware.task_distribution import distance, random

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

    def __init__(self) -> None:
        """Construct object instance."""
        self.tes_uris: List[str] = []
        self.input_uris: List[str] = []

    def modify_request(self, request):
        """Add ranked list of TES instances to request body.

        Args:
            request: Incoming request object.

        Returns:
            Tuple of modified request object and start time.
        """
        start_time = datetime.now().strftime("%m-%d-%Y %H:%M:%S")

        if "inputs" in request.json.keys():
            for index in range(len(request.json["inputs"])):
                if "url" in request.json["inputs"][index].keys():
                    self.input_uris.append(
                        request.json["inputs"][index]["url"]
                    )
                else:
                    continue

        if len(self.input_uris) != 0:
            self.tes_uris = distance.task_distribution(self.input_uris)
        else:
            self.tes_uris = random.task_distribution()

        if len(self.tes_uris) != 0:
            request.json["tes_uri"] = self.tes_uris
        else:
            raise Exception  # pragma pylint: disable=broad-exception-raised
        return request, start_time
