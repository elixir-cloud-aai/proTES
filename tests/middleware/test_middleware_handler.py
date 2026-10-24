"""Tests of the middleware handler."""

from typing import Any

import pytest

from pro_tes.exceptions import InvalidMiddleware, MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware
from pro_tes.middleware.middleware_handler import MiddlewareHandler

MODULE = __name__


class AddA(AbstractMiddleware):
    """Middleware appending `A` to `steps` of the task."""

    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Append `A`."""
        task["steps"].append("A")
        return task


class AddB(AbstractMiddleware):
    """Middleware appending `B` to `steps` of the task, in a new task."""

    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Append `B`."""
        return {**task, "steps": [*task["steps"], "B"]}


class Failing(AbstractMiddleware):
    """Middleware that fails."""

    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Fail.

        Raises:
            MiddlewareException: Always.
        """
        raise MiddlewareException("failed")


class ChangeThenFail(AbstractMiddleware):
    """Middleware that changes the task, then fails."""

    def apply_middleware(self, task: dict[str, Any]) -> dict[str, Any]:
        """Append `X` and add a field, then fail.

        Raises:
            MiddlewareException: Always.
        """
        task["steps"].append("X")
        task["added"] = True
        raise MiddlewareException("failed")


class NoTask(AbstractMiddleware):
    """Middleware that changes the task, but returns no task."""

    def apply_middleware(self, task: dict[str, Any]) -> Any:
        """Append `X`, return `None`."""
        task["steps"].append("X")
        return None


class NotAMiddleware:
    """Class that is not a middleware."""


def apply_task(paths: list[str | list[str]], task: dict[str, Any]) -> dict[str, Any]:
    """Apply middlewares to a task, checking that it is not changed.

    Args:
        paths: Import paths of the middlewares, as in the config.
        task: Task.

    Returns:
        The task after the middlewares.
    """
    handler = MiddlewareHandler()
    handler.set_middlewares(paths=paths)
    original = repr(task)
    result = handler.apply_middlewares(task=task)
    assert repr(task) == original
    return result


def apply(paths: list[str | list[str]]) -> list[str]:
    """Apply middlewares to a task, returning the steps they added.

    Args:
        paths: Import paths of the middlewares, as in the config.

    Returns:
        The steps that the middlewares added to the task.
    """
    steps: list[str] = apply_task(paths=paths, task={"steps": []})["steps"]
    return steps


def test_set_middlewares() -> None:
    """Middlewares are imported, single ones and lists of alternatives."""
    handler = MiddlewareHandler()
    handler.set_middlewares(
        paths=[f"{MODULE}.AddA", [f"{MODULE}.AddB", f"{MODULE}.AddA"]]
    )
    assert handler.middlewares == [[AddA], [AddB, AddA]]


def test_apply_middlewares() -> None:
    """Middlewares are applied in order, to copies of the task."""
    assert apply([f"{MODULE}.AddB", f"{MODULE}.AddA"]) == ["B", "A"]


def test_apply_middlewares_fallback() -> None:
    """The next alternative is applied if a middleware fails."""
    assert apply([[f"{MODULE}.Failing", f"{MODULE}.AddA", f"{MODULE}.AddB"]]) == ["A"]


@pytest.mark.parametrize("failing", ["ChangeThenFail", "NoTask"])
def test_apply_middlewares_fallback_isolated(failing: str) -> None:
    """Changes of failed alternatives are discarded before the next one.

    Alternatives that return no task fail, too.
    """
    paths: list[str | list[str]] = [
        f"{MODULE}.AddA",
        [f"{MODULE}.{failing}", f"{MODULE}.{failing}", f"{MODULE}.AddB"],
    ]
    assert apply_task(paths=paths, task={"steps": []}) == {"steps": ["A", "B"]}


def test_apply_middlewares_all_fail() -> None:
    """Requests fail if all alternatives of a middleware fail."""
    with pytest.raises(MiddlewareException, match="No middleware could be applied"):
        apply([f"{MODULE}.AddA", [f"{MODULE}.Failing", f"{MODULE}.NoTask"]])


@pytest.mark.parametrize(
    "path, message",
    [
        ("AddA", "Invalid middleware string"),
        ("tests.middleware.missing.AddA", "Could not import module"),
        (f"{MODULE}.Missing", "does not contain a class called Missing"),
        (f"{MODULE}.NotAMiddleware", "does not inherit from"),
    ],
)
def test_invalid_middleware(path: str, message: str) -> None:
    """Paths that are not of a middleware class are rejected."""
    with pytest.raises(InvalidMiddleware, match=message):
        MiddlewareHandler().set_middlewares(paths=[path])
