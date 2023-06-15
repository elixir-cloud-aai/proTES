import importlib
import logging
import requests

from flask import current_app


logger = logging.getLogger(__name__)


class MiddlewarePipeline:
    def __init__(self, middlewares=None):
        self.middlewares = middlewares or []
        logger.info(f"middleware: {self.middlewares}")

    def add_middleware(self, middleware):
        self.middlewares.append(middleware)

    def process_request(self, request, *args, **kwargs) -> requests.Request:
        for middleware in self.middlewares:
            # Invoke the abstract method _set_request
            request = middleware.set_request(request, *args, **kwargs)
            # for attr_name in dir(middleware):
            #     if attr_name.startswith('_set_'):
            #         set_func = getattr(middleware, attr_name)
            #         set_func(request)
        return request


def load_middlewares_from_config(config: dict) -> list:
    middlewares = []
    for middleware_group in config.values():
        for middleware_path in middleware_group:
            module_path, class_name = middleware_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            middleware_class = getattr(module, class_name)
            middlewares.append(middleware_class())
    return middlewares


def create_middleware_pipeline() -> MiddlewarePipeline:
    middleware_config = current_app.config.foca.middlewares
    middlewares = load_middlewares_from_config(middleware_config)
    logger.info(f"Loaded middlewares: {middlewares}")
    pipeline = MiddlewarePipeline(middlewares)
    logger.info(f"Created middleware pipeline: {pipeline}")
    return pipeline
