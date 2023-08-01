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
        for main_middleware, fallback_middlewares in self.middlewares:
            try:
                # Process the main middleware
                request = main_middleware.set_request(request, *args, **kwargs)
            except Exception as main_exception:  # pylint: disable=W0718
                logger.exception("Error occurred in main middleware: %s",
                                 main_exception)
                # Main middleware failed, process the fallback middlewares
                for fallback_middleware in fallback_middlewares:
                    try:
                        request = fallback_middleware.set_request(
                            request,
                            *args,
                            **kwargs
                        )
                        break  # Exit the fallback loop if a fallback succeeded
                    except Exception \
                            as fallback_exception:  # pylint: disable=W0718
                        logger.exception(
                            "Error occurred in fallback middleware: %s",
                            fallback_exception)
                        # Fallback middleware also failed, continue to the next
                        # fallback
            else:
                continue

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
                [
                    MainMiddleware1(),
                    [
                        FallbackMiddleware1(),
                        FallbackMiddleware2(),
                        FallbackMiddleware3()
                    ]
                ],
            [
                MainMiddleware2(),
                [
                    FallbackMiddleware4(),
                    FallbackMiddleware5(),
                    FallbackMiddleware6()
                ]
            ]
        ].
    """
    middlewares = []

    for middleware_group_list in config.values():
        for middleware_group in middleware_group_list:
            main_middleware_path, fallback_middleware_paths = middleware_group
            main_middleware = load_middleware_instance(main_middleware_path)

            fallback_middlewares = [load_middleware_instance(path) for path in
                                    fallback_middleware_paths]

            middlewares.append([main_middleware, fallback_middlewares])

    return middlewares


def create_middleware_pipeline() -> MiddlewarePipeline:
    """Create middleware pipeline."""
    middleware_config = current_app.config.foca.middlewares
    middlewares = load_middlewares_from_config(middleware_config)
    logger.info(f"Loaded middlewares: {middlewares}")
    pipeline = MiddlewarePipeline(middlewares)
    logger.info(f"Created middleware pipeline: {pipeline}")
    return pipeline
