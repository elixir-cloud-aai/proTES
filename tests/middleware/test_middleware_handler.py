"""Tests of the middleware handler."""

from typing import Any

import flask
import pytest

from pro_tes.exceptions import InvalidMiddleware, MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware
from pro_tes.middleware.middleware_handler import MiddlewareHandler

MODULE = __name__


class AddA(AbstractMiddleware):
    """Middleware appending `A` to `steps` of the request body."""

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Append `A`."""
        assert request.json is not None
        request.json["steps"].append("A")
        return request


class AddB(AbstractMiddleware):
    """Middleware appending `B` to `steps` of the request body."""

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Append `B`."""
        assert request.json is not None
        request.json["steps"].append("B")
        return request


class Failing(AbstractMiddleware):
    """Middleware that fails."""

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Fail.

        Raises:
            MiddlewareException: Always.
        """
        raise MiddlewareException("failed")


class ChangeThenFail(AbstractMiddleware):
    """Middleware that changes the request body, then fails."""

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Append `X` and add a field, then fail.

        Raises:
            MiddlewareException: Always.
        """
        assert request.json is not None
        request.json["steps"].append("X")
        request.json["added"] = True
        raise MiddlewareException("failed")


class Noop(AbstractMiddleware):
    """Middleware that changes nothing."""

    def apply_middleware(self, request: flask.Request) -> flask.Request:
        """Return the request as it is."""
        return request


class NotAMiddleware:
    """Class that is not a middleware."""


def apply_body(paths: list[str | list[str]], body: Any) -> Any:
    """Apply middlewares to a request, returning its body.

    Args:
        paths: Import paths of the middlewares, as in the config.
        body: JSON body of the request.

    Returns:
        The request body after the middlewares.
    """
    handler = MiddlewareHandler()
    handler.set_middlewares(paths=paths)
    app = flask.Flask(__name__)
    with app.test_request_context(json=body):
        request = handler.apply_middlewares(request=flask.request)
        return request.json


def apply(paths: list[str | list[str]]) -> list[str]:
    """Apply middlewares to a request, returning the steps they added.

    Args:
        paths: Import paths of the middlewares, as in the config.

    Returns:
        The steps that the middlewares added to the request body.
    """
    steps: list[str] = apply_body(paths=paths, body={"steps": []})["steps"]
    return steps


def test_set_middlewares() -> None:
    """Middlewares are imported, single ones and lists of alternatives."""
    handler = MiddlewareHandler()
    handler.set_middlewares(
        paths=[f"{MODULE}.AddA", [f"{MODULE}.AddB", f"{MODULE}.AddA"]]
    )
    assert handler.middlewares == [[AddA], [AddB, AddA]]


def test_apply_middlewares() -> None:
    """Middlewares are applied in order."""
    assert apply([f"{MODULE}.AddB", f"{MODULE}.AddA"]) == ["B", "A"]


def test_apply_middlewares_fallback() -> None:
    """The next alternative is applied if a middleware fails."""
    assert apply([[f"{MODULE}.Failing", f"{MODULE}.AddA", f"{MODULE}.AddB"]]) == ["A"]


def test_apply_middlewares_fallback_isolated() -> None:
    """Changes of failed alternatives are undone before the next one."""
    paths: list[str | list[str]] = [
        f"{MODULE}.AddA",
        [f"{MODULE}.ChangeThenFail", f"{MODULE}.ChangeThenFail", f"{MODULE}.AddB"],
    ]
    assert apply_body(paths=paths, body={"steps": []}) == {"steps": ["A", "B"]}


def test_apply_middlewares_fallback_not_object() -> None:
    """Bodies that are not objects are passed on as they are."""
    assert apply_body(paths=[[f"{MODULE}.Failing", f"{MODULE}.Noop"]], body=[1]) == [1]


def test_apply_middlewares_all_fail() -> None:
    """Requests fail if all alternatives of a middleware fail."""
    with pytest.raises(MiddlewareException, match="No middleware could be applied"):
        apply([f"{MODULE}.AddA", [f"{MODULE}.Failing"]])


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
