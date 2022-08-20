import abc
from typing import (
    Dict,
)

from pro_tes.task_distribution.task_distribution import task_distribution


class AbstractMiddleware(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def add_tes_endpoint(self, request_body):
        pass


class Middleware(AbstractMiddleware):

    def add_tes_endpoint(self, request_body) -> Dict:
        tes_uri = task_distribution()
        if len(tes_uri) != 0:
            request_body.json['tes_uri'] = tes_uri
        else:
            raise Exception
        return request_body
