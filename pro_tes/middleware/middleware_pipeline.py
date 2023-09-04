"""Set up the middleware pipeline."""
import importlib
import logging
from typing import Type
import requests

from pro_tes.exceptions import InvalidMiddleware
from pro_tes.middleware.middleware import AbstractMiddleware

logger = logging.getLogger(__name__)


class MiddlewarePipeline:
    """
    Middleware Pipeline.

    A class for managing and processing incoming requests through a sequence of
    middleware components.

    Methods:
        __init__(self, middlewares=None): Initializes a MiddlewarePipeline
            instance, optionally with an initial list of middleware components.
        add_middleware(self, middleware): Adds a middleware component to the
            pipeline.
        process_request(self, request, *args, **kwargs): Processes an incoming
            request through the middleware pipeline, applying each
            middlewares logic in sequence.
    """

    def __init__(self, middlewares=None):
        """
        Initialize a MiddlewarePipeline instance.

        Args:
            middlewares (list, optional): A list of middleware components to
            initialize the pipeline. Defaults to an empty list if not provided.

        Attributes:
            middlewares (list): A list that holds the middleware components
            in the pipeline.
        """
        self.middlewares = middlewares or []
        logger.info(f"middleware: {self.middlewares}")

    def add_middleware(self, middleware) -> None:
        """Add a middleware to the pipeline."""
        self.middlewares.append(middleware)

    def process_request(
            self,
            request: requests.Request,
            *args,
            **kwargs
    ) -> requests.Request:
        """
        Process the incoming request through the middleware pipeline.

        This method iterates through the list of middleware components in the
        pipeline and calls the `set_request` method of each component to
        process the incoming request.

        Args:
            request: The incoming requests.Request object.
            *args: Additional positional arguments to pass to the middleware.
            **kwargs: Additional keyword arguments to pass to the middleware.

        Returns:
            requests.Request object tha is the modified request object after
            processing by all middleware components.

        Raises:
            ValueError: If a list of middleware components is provided,and all
            of them fail to process the request, this exception is raised.
        """
        for middleware in self.middlewares:
            logger.info(
                f"trying to execute the middleware {middleware}"
            )
            if isinstance(middleware, list):
                inner_success = False
                for mid_instance in middleware:
                    try:
                        request = mid_instance.set_request(
                            request,
                            *args,
                            **kwargs
                        )
                        inner_success = True
                        break
                    except Exception as exc:  # pylint: disable=W0703
                        logger.exception(
                            f"Error occurred in middleware: {exc}"
                        )
                if not inner_success:
                    raise ValueError(
                        f"List of Failed Middlewares {middleware} "
                    )
            else:
                try:
                    request = middleware.set_request(
                        request,
                        *args,
                        **kwargs
                    )
                except Exception as exc:  # pylint: disable=W0703
                    logger.exception(
                        f"Error occurred in middleware: {exc}"
                    )
        return request


class MiddlewareLoader:
    """A class for loading middleware instances based on configuration."""

    def __init__(self):
        """Initialize a MiddlewareLoader instance."""
        self.middleware_list = []

    def load_middleware_instance(
            self,
            middleware_path: str
    ) -> Type[AbstractMiddleware]:
        """Load a middleware instance.

        Args:
            middleware_path: Middleware path in the form:
                "module.submodule.MiddlewareClass".

        Returns:
            Middleware instance.
        """
        module_path, class_name = middleware_path.rsplit('.', 1)
        module = importlib.import_module(module_path)
        middleware_class = getattr(module, class_name)
        if not issubclass(middleware_class, AbstractMiddleware):
            raise InvalidMiddleware

        return middleware_class()

    def load_middlewares_from_config(self, config: dict) -> list:
        """Load all middlewares from config.

        Args:
             config: Middleware config.

        Returns:
            List of middleware objects in the form:
            [
                mw1(),
                [
                    mw2(),
                    mw2_fb1(),
                    mw2_fb2()
                ]
                mw3(),
            ].
        """
        for key in config:
            if isinstance(config[key], list):
                self.middleware_list.extend(config[key])

        new_middleware_list = []

        for item in self.middleware_list:
            if isinstance(item, list):
                new_sublist = [
                    self.load_middleware_instance(subitem) for subitem in item
                ]
                new_middleware_list.append(new_sublist)
            else:
                new_middleware_list.append(self.load_middleware_instance(item))

        return new_middleware_list
