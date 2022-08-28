import abc
from typing import (
    Dict,
)

from pro_tes.task_distribution.task_distribution import task_distribution


class AbstractMiddleware(metaclass=abc.ABCMeta):
    """Abstract class to implement different middleware."""

    @abc.abstractmethod
    def modify_request(self, request):
        pass


class TaskDistributionMiddleware(AbstractMiddleware):
    """Calls a task distribution logic which returns a list of the best /
    tes_uri to submit the task on.
    """

    def __init__(self):
        """Return : list of TES instance best suited for TES ask."""
        self.tes_uri = task_distribution()

    def modify_request(self, request) -> Dict:
        """Add TES instance to the request body."""
        if len(self.tes_uri) != 0:
            request.json['tes_uri'] = self.tes_uri
        else:
            raise Exception
        return request
