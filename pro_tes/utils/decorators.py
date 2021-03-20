"""Custom decorators."""

import logging
from functools import wraps
from typing import (Callable)

from connexion import request
from flask import current_app

from pro_tes.config.config_parser import get_conf
from pro_tes.security.process_jwt import JWT

# Get logger instance
logger = logging.getLogger(__name__)


def auth_token_optional(fn: Callable) -> Callable:
    """
    The decorator protects an endpoint from being called without a valid
    authorization token.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):

        # Check if authentication is enabled
        if get_conf(
            current_app.config,
            'security',
            'authorization_required',
        ):

            jwt = JWT(request=request)
            jwt.validate()
            jwt.get_user()
            ## Create JWT instance
            #try:
            #    jwt = JWT(request=request)
            #except Exception as e:
            #    raise Unauthorized from e

            ## Validate JWT
            #try:
            #    jwt.validate()
            #except Exception as e:
            #    raise Unauthorized from e

            ## Get user ID
            #try:
            #    jwt.get_user()
            #except Exception as e:
            #    raise Unauthorized from e

            # Return wrapped function with token data
            return fn(
                jwt=jwt.jwt,
                claims=jwt.claims,
                user_id=jwt.user,
                *args,
                **kwargs
            )

        # Return wrapped function without token data
        else:
            return fn(*args, **kwargs)

    return wrapper
