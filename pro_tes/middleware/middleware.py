"""Middleware to inject into TES requests."""

from typing import List
import requests

from pro_tes.middleware_handler.abstract_middleware import AbstractMiddleware
from pro_tes.exceptions import (
    NoTesInstancesAvailable,
    TesUriError,
    InputUriError,
    IPDistanceCalculationError,
)
from pro_tes.middleware.task_distribution import distance, random

# pragma pylint: disable=too-few-public-methods


class DistanceTaskDistribution(AbstractMiddleware):
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

        self.tes_uris = self._set_url(self.input_uris)

        if self.tes_uris:
            request.json["tes_uri"] = self.tes_uris
        else:
            raise NoTesInstancesAvailable
        return request

    def _set_url(self, input_uris: List[str]) -> List[str]:
        """Set the TES URI.

        Args:
            input_uris: A list of input URIs from the incoming request.

        Returns:
             List of TES URIs.
        """
        try:
            tes_uris = distance.task_distribution(input_uris)
        except (
            TesUriError,
            InputUriError,
            IPDistanceCalculationError,
            KeyError,
            ValueError
        ) as exc:
            raise NoTesInstancesAvailable from exc
        return tes_uris


class RandomTaskDistribution(AbstractMiddleware):
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
        self.tes_uris = self._set_url()

        if self.tes_uris:
            request.json["tes_uri"] = self.tes_uris
        else:
            raise NoTesInstancesAvailable
        return request

    def _set_url(self) -> List[str]:
        """Set the TES URI.

        Args:
            input_uris: A list of input URIs from the incoming request.

        Returns:
             List of TES URIs.
        """
        try:
            tes_uris = random.task_distribution()
        except Exception as exc:
            raise NoTesInstancesAvailable from exc
        return tes_uris
