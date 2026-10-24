"""Middleware handler."""

from collections.abc import Sequence
from copy import deepcopy
import importlib
import logging
from typing import Any

from pro_tes.exceptions import InvalidMiddleware, MiddlewareException
from pro_tes.middleware.abstract_middleware import AbstractMiddleware

logger = logging.getLogger(__name__)


class MiddlewareHandler:
    """Manage middlewares and apply them to a task.

    Attributes:
        middlewares: List of middleware classes with up to one level of
            nesting.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.middlewares: list[list[type[AbstractMiddleware]]] = []

    def set_middlewares(self, paths: Sequence[str | list[str]]) -> None:
        """Import and set middlewares from paths.

        An example of aected input format:
            [
                'package.middlewares.one,
                ['package.middlewares.twoA', 'package.middlewares.twoB'],
                ['package.middlewares.threeA', 'package.middlewares.threeB'],
                'package.middlewares.four',
            ]

        Args:
            paths: List of import paths for the middleware classes to be
                imported, with up to one level of nesting.
        """
        for item in paths:
            if isinstance(item, list):
                self.middlewares.append(
                    [self._import_middleware_class(path) for path in item]
                )
            else:
                self.middlewares.append([self._import_middleware_class(item)])

    def apply_middlewares(self, task: dict[str, Any]) -> dict[str, Any]:
        """Apply middlewares to a task.

        This method iterates through the list of available middlewares and
        attempts to apply them in order. Each middleware can be a single class
        or a list of classes. The latter provides a fallback mechanism in case
        a middleware fails to be applied. In that case, the next middleware in
        the list is attempted.

        Each alternative gets its own copy of the task, which is kept only if
        the alternative succeeds: changes of failed alternatives are
        discarded, so every alternative gets the task as it was before the
        step. Alternatives fail if they raise an exception, or return
        something other than a task (an object).

        Args:
            task: Task (a TES task, as a JSON object); not changed.

        Returns:
            The task, as changed by the middlewares.

        Raises:
            MiddlewareException: If a middleware (a single class or all classes
                in a list of alternatives) could not be applied.
        """
        for middleware in self.middlewares:
            for mw_class in middleware:
                logger.info("Applying middleware: %s", mw_class)
                instance = mw_class()
                try:
                    result = instance.apply_middleware(deepcopy(task))
                except Exception as exc:
                    logger.warning(
                        "Error occurred in middleware class '%s': %s", mw_class, exc
                    )
                    continue
                if not isinstance(result, dict):
                    logger.warning(
                        "Middleware class '%s' returned no task, but: %s",
                        mw_class,
                        type(result).__name__,
                    )
                    continue
                task = result
                break
            else:
                raise MiddlewareException("No middleware could be applied.")
        return task

    @staticmethod
    def _import_middleware_class(import_path: str) -> type[AbstractMiddleware]:
        """Import a middleware class by its import path.

        Args:
            import_path: Fully qualified import path for the middleware class
                to be improrted, e.g., 'package.module.MiddlewareClass'.

        Returns:
            Middleware class.

        Raises:
            InvalidMiddleware: If the middleware path is invalid.
        """
        try:
            module_path, class_name = import_path.rsplit(".", 1)
        except ValueError as exc:
            raise InvalidMiddleware("Invalid middleware string.") from exc
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise InvalidMiddleware(f"Could not import module: {module_path}.") from exc
        try:
            middleware_class = getattr(module, class_name)
        except AttributeError as exc:
            raise InvalidMiddleware(
                f"Module {module_path} does not contain a class called {class_name}."
            ) from exc
        if not issubclass(middleware_class, AbstractMiddleware):
            raise InvalidMiddleware(
                "Middleware class does not inherit from "
                "'pro_tes.middleware.middleware.AbstractMiddleware'."
            )
        return middleware_class
