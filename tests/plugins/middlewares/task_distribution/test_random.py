"""Tests of the random task distribution middleware and its base class."""

import random

from connexion import FlaskApp
import flask
import pytest

from pro_tes.exceptions import MiddlewareException
from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)
from pro_tes.plugins.middlewares.task_distribution.random import (
    TaskDistributionRandom,
)
from pro_tes.utils.misc import get_custom_config

TES_URLS = [f"https://tes{index}.example.org" for index in range(10)]


@pytest.mark.parametrize(
    "middleware", [TaskDistributionBaseClass, TaskDistributionRandom]
)
def test_tes_urls_added(
    app: FlaskApp, middleware: type[TaskDistributionBaseClass]
) -> None:
    """The configured backends are added to the request, once each."""
    with app.app.test_request_context(json={"name": "task"}):
        get_custom_config().tes.service_list = [*TES_URLS, TES_URLS[0]]
        request = middleware().apply_middleware(flask.request)
        assert request.json is not None
        assert request.json["name"] == "task"
        assert sorted(request.json["tes_urls"]) == TES_URLS


def test_shuffled(app: FlaskApp) -> None:
    """The random middleware shuffles the backends."""
    random.seed(1)
    orders = set()
    with app.app.test_request_context(json={}):
        get_custom_config().tes.service_list = TES_URLS
        for _ in range(5):
            middleware = TaskDistributionRandom()
            middleware.apply_middleware(flask.request)
            orders.add(tuple(middleware.tes_urls))
    assert len(orders) > 1


def test_no_json(app: FlaskApp) -> None:
    """Requests without a JSON body fail the middleware."""
    with (
        app.app.test_request_context(data="null", content_type="application/json"),
        pytest.raises(MiddlewareException, match="no JSON payload"),
    ):
        TaskDistributionRandom().apply_middleware(flask.request)
