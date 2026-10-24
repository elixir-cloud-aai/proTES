"""Tests of the random task distribution middleware and its base class."""

import random

from connexion import FlaskApp
import pytest

from pro_tes.config_models import TesBackend
from pro_tes.plugins.middlewares.task_distribution.base import (
    TaskDistributionBaseClass,
)
from pro_tes.plugins.middlewares.task_distribution.random import (
    TaskDistributionRandom,
)
from pro_tes.utils.misc import get_custom_config

TES_URLS = [f"https://tes{index}.example.org" for index in range(10)]


def backends(urls: list[str]) -> list[TesBackend]:
    """Get the configurations of TES backends.

    Args:
        urls: URLs of the backends.

    Returns:
        Configurations of backends at the URLs, without authentication.
    """
    return [TesBackend(url=url, auth="none") for url in urls]


@pytest.mark.parametrize(
    "middleware", [TaskDistributionBaseClass, TaskDistributionRandom]
)
def test_tes_urls_added(
    app: FlaskApp, middleware: type[TaskDistributionBaseClass]
) -> None:
    """The configured backends are added to the task, once each."""
    with app.app.app_context():
        get_custom_config().tes.service_list = backends([*TES_URLS, TES_URLS[0]])
        task = middleware().apply_middleware({"name": "task"})
        assert task["name"] == "task"
        assert sorted(task["tes_urls"]) == TES_URLS


def test_order_kept(app: FlaskApp) -> None:
    """The base class keeps the order of the backends (first occurrences)."""
    urls = list(reversed(TES_URLS))
    with app.app.app_context():
        get_custom_config().tes.service_list = backends([*urls, urls[0]])
        task = TaskDistributionBaseClass().apply_middleware({})
        assert task["tes_urls"] == urls


def test_shuffled_reproducibly(app: FlaskApp) -> None:
    """The random order depends on the random state only."""
    orders = set()
    with app.app.app_context():
        get_custom_config().tes.service_list = backends(TES_URLS)
        for _ in range(3):
            random.seed(1)
            middleware = TaskDistributionRandom()
            middleware.apply_middleware({})
            orders.add(tuple(middleware.tes_urls))
    assert len(orders) == 1


def test_shuffled(app: FlaskApp) -> None:
    """The random middleware shuffles the backends."""
    random.seed(1)
    orders = set()
    with app.app.app_context():
        get_custom_config().tes.service_list = backends(TES_URLS)
        for _ in range(5):
            middleware = TaskDistributionRandom()
            middleware.apply_middleware({})
            orders.add(tuple(middleware.tes_urls))
    assert len(orders) > 1
