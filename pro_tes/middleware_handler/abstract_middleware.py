"""Parent Abstract Class for all the middlewares"""

import abc
import requests


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
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """