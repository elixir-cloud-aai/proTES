"""Middleware to inject into TES requests."""

import abc
import requests
from typing import List

from pro_tes.exceptions import (
    NoTesInstancesAvailable,
    TesUriError,
    InputUriError,
    IPDistanceCalculationError,
)
from pro_tes.middleware.task_distribution import distance, random

# pragma pylint: disable=too-few-public-methods


class AbstractMiddleware(metaclass=abc.ABCMeta):
    """Abstract class to implement different middleware."""

    @abc.abstractmethod
    def set_request(
        self,
        request: requests.Request,
        *args,
        **kwargs
    ) -> requests.Request:
        """Set the incoming request object.

        Abstract method.

        Args:
            request: The incoming request object.
        """


class TaskDistributionMiddleware(AbstractMiddleware):
    """Inject task distribution logic.

    Attributes:
        tes_uri: TES instance best suited for TES task.
        input_uris: A list of input URIs from the incoming request.
    """

    def __init__(self) -> None:
        """Construct object instance."""
        self.tes_uris: List[str] = []
        self.input_uris: List[str] = []

    def set_request(self, request: requests.Request, *args, **kwargs):
        """Modify the incoming task request.

        Abstract method

        Args:
            request: Incoming request object.

        Returns:
            The modified request object.

        Raises:
            pro_tes.exceptions.NoTesInstancesAvailable: If no valid TES
                instances are available.
        """
        if "inputs" in request.json.keys():
            for index in range(len(request.json["inputs"])):
                if "url" in request.json["inputs"][index].keys():
                    self.input_uris.append(
                        request.json["inputs"][index]["url"]
                    )

        try:
            self.tes_uris = distance.task_distribution(self.input_uris)
        except (
            TesUriError,
            InputUriError,
            IPDistanceCalculationError,
            KeyError,
            ValueError
        ):
            self.tes_uris = random.task_distribution()

        if self.tes_uris:
            request.json["tes_uri"] = self.tes_uris
        else:
            raise NoTesInstancesAvailable
        return request
