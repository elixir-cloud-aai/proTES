"""Set up the middleware pipeline."""
import importlib
import logging

from flask import current_app

logger = logging.getLogger(__name__)


class MiddlewarePipeline:
    """Middleware pipeline."""

    def __init__(self, middlewares=None):
        """Construct object instance."""
        self.middlewares = middlewares or []
        logger.info(f"middleware: {self.middlewares}")

    def add_middleware(self, middleware):
        """Add a middleware to the pipeline."""
        self.middlewares.append(middleware)

    def process_request(self, request, *args, **kwargs):
        """Process the incoming request.

        Args:
            request: Incoming request object.
            *args: Additional positional arguments.
            ***kwargs: Additional keyword arguments.

        Returns:
            The modified request object.
        """
        for middleware in self.middlewares:
            if isinstance(middleware, list):
                inner_success = False
                for m in middleware:
                    try:
                        request = m.set_request(
                            request,
                            *args,
                            **kwargs
                        )
                        inner_success = True
                        break
                    except Exception as exc:  # pylint: disable=W0703
                        logger.exception(
                            "Error occurred in inner middleware: %s",
                            exc
                        )
                if not inner_success:
                    raise Exception("inner middleware failed")
            else:
                try:
                    request = middleware.set_request(
                        request,
                        *args,
                        **kwargs
                    )
                except Exception as exc:  # pylint: disable=W0703
                    logger.exception(
                        "Error occurred in middleware: %s",
                        exc
                    )
        return request


def load_middleware_instance(middleware_path):
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
    return middleware_class()


def load_middlewares_from_config(config: dict) -> list:
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

    middleware_list = []

    for key in config:
        if isinstance(config[key], list):
            middleware_list.extend(config[key])

    new_middleware_list = []

    for item in middleware_list:
        if isinstance(item, list):
            new_sublist = [load_middleware_instance(subitem) for subitem in
                           item]
            new_middleware_list.append(new_sublist)
        else:
            new_middleware_list.append(load_middleware_instance(item))

    return new_middleware_list


def create_middleware_pipeline() -> MiddlewarePipeline:
    """Create middleware pipeline."""
    middleware_config = current_app.config.foca.middlewares
    middlewares = load_middlewares_from_config(middleware_config)
    logger.info(f"Loaded middlewares: {middlewares}")
    pipeline = MiddlewarePipeline(middlewares)
    logger.info(f"Created middleware pipeline: {pipeline}")
    return pipeline
