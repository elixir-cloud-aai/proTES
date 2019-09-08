"""Decorator and utility functions for protecting access to endpoints."""

from connexion.exceptions import Unauthorized
from connexion import request
from flask import current_app
from functools import wraps
import logging
from typing import (Callable, Dict, List, Mapping, Union)

from jwt import (decode, get_unverified_header, algorithms)
import requests
import json

from pro_tes.config.config_parser import get_conf, get_conf_type
from pro_tes.security.utils import (
    check_get_request_with_jwt_header,
    get_idp_service_info_from_jwt_issuer_claim,
    parse_jwt_from_request
)
from pro_tes.utils.utils import missing_from_dict


# Get logger instance
logger = logging.getLogger(__name__)


def auth_token_optional(fn: Callable) -> Callable:
    """
    **The decorator protects an endpoint from being called without a valid
    authorization token.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):

        # Check if authentication is enabled
        if get_conf(
            current_app.config,
            'security',
            'authorization_required'
        ):

            # Get config parameters
            validation_methods = get_conf_type(
                current_app.config,
                'security',
                'jwt',
                'validation_methods',
                types=(List),
            )
            validation_checks = get_conf(
                current_app.config,
                'security',
                'jwt',
                'validation_checks',
            )
            algorithms = get_conf_type(
                current_app.config,
                'security',
                'jwt',
                'algorithms',
                types=(List),
            )
            expected_prefix = get_conf(
                current_app.config,
                'security',
                'jwt',
                'token_prefix'
            )
            header_name = get_conf(
                current_app.config,
                'security',
                'jwt',
                'header_name'
            )
            claim_key_id = get_conf(
                current_app.config,
                'security',
                'jwt',
                'claim_key_id'
            )
            claim_issuer = get_conf(
                current_app.config,
                'security',
                'jwt',
                'claim_issuer'
            )
            claim_identity = get_conf(
                current_app.config,
                'security',
                'jwt',
                'claim_identity'
            )

            # Ensure that at least one validation method was configured
            if not len(validation_methods):
                logger.error("No JWT validation methods configured.")
                raise Unauthorized

            # Ensure that a valid validation checks argument was configured
            if validation_checks == 'any':
                required_validations = 1
            elif validation_checks == 'all':
                required_validations = len(validation_methods)
            else:
                logger.error(
                    (
                        "Illegal argument '{validation_checks} passed to "
                        "configuration paramater 'validation_checks'. Allowed "
                        "values: 'any', 'all'"
                    )
                )
                raise Unauthorized

            # Parse JWT token from HTTP header
            jwt = parse_jwt_from_request(
                request=request,
                header_name=header_name,
                prefix=expected_prefix,
            )

            # Initialize validation counter
            validated = 0

            # Validate JWT via /userinfo endpoint
            if 'userinfo' in validation_methods \
                and validated < required_validations:
                logger.info(
                    (
                        "Validating JWT via identity provider's '/userinfo' "
                        "endpoint..."
                    )
                )
                claims = validate_jwt_via_userinfo_endpoint(
                    jwt=jwt,
                    algorithms=algorithms,
                    claim_issuer=claim_issuer,
                )
                if claims:
                    validated += 1

            # Validate JWT via public key
            if 'public_key' in validation_methods \
                and validated < required_validations:
                logger.info(
                    (
                        "Validating JWT via identity provider's public key..."
                    )
                )
                claims = validate_jwt_via_public_key(
                    jwt=jwt,
                    algorithms=algorithms,
                    claim_key_id=claim_key_id,
                    claim_issuer=claim_issuer,
                )
                if claims:
                    validated += 1
            
            # Check whether enough validation checks passed
            if not validated == required_validations:
                logger.error(
                    (
                        "Insufficient number of JWT validation checks passed."
                    )
                )
                raise Unauthorized

            # Ensure that specified identity claim is available
            if missing_from_dict(
                claim_identity,
                dictionary=claims,
            ):
                raise KeyError(
                    f"Required claim '{claim_identity}' missing from JWT "\
                    f"claims."
                )

            # Return wrapped function with token data
            return fn(
                jwt=jwt,
                claims=claims,
                user_id=claims[claim_identity],
                *args,
                **kwargs
            )

        # Return wrapped function without token data
        else:
            return fn(*args, **kwargs)

    return wrapper


def validate_jwt_via_userinfo_endpoint(
    jwt: str,
    algorithms: List[str] = ['RS256'],
    claim_issuer: str = 'iss',
    service_document_field: str = 'userinfo_endpoint',
) -> Dict:

    # Decode JWT
    try:
        claims = decode(
            jwt=jwt,
            verify=False,
            algorithms=algorithms
        )
    except Exception as e:
        logger.warning(
            f"JWT could not be decoded. Original error message: "
            f"{type(e).__name__}: {e}"
        )
        return {}

    # Verify existence of issuer claim
    if missing_from_dict(
        claim_issuer,
        dictionary=claims,
    ):
        logger.warning(
            f"Required claim '{claim_issuer}' missing from JWT claims."
        )
        return {}

    # Get /userinfo endpoint URL
    url = get_idp_service_info_from_jwt_issuer_claim(
        issuer=claims[claim_issuer],
    )

    # Validate JWT via /userinfo endpoint
    try:
        check_get_request_with_jwt_header(
            url=url,
            jwt=jwt,
        )
    except Exception:
        return {}

    return claims


def validate_jwt_via_public_key(
    jwt: str,
    algorithms: List[str] = ['RS256'],
    claim_key_id: str = 'kid',
    claim_issuer: str = 'iss',
    service_document_field: str = 'jwks_uri',
) -> Dict:

    # Extract JWT claims
    try:
        claims = decode(
            jwt=jwt,
            verify=False,
            algorithms=algorithms,
        )
    except Exception as e:
        logger.warning(
            f"JWT could not be decoded. Original error message: " \
            f"{type(e).__name__}: {e}"
        )
        return {}

    # Extract JWT header claims
    try:
        header_claims = get_unverified_header(jwt)
    except Exception as e:
        logger.warning(
            f"Could not extract JWT header claims. Original error message: " \
            f"{type(e).__name__}: {e}"
        )
        return {}

    # Verify existence of key ID claim
    if not missing_from_dict(
        claim_key_id,
        dictionary=header_claims,
    ):
        logger.warning(
            f"Required claim '{claim_key_id}' missing from JWT claims."
        )
        return {}

    # Get JWK set endpoint URL
    url = get_idp_service_info_from_jwt_issuer_claim(
        issuer=claims[claim_issuer],
    )

    # Obtain identity provider's public keys
    public_keys = get_public_keys(
        url=url,
        claim_key_id=claim_key_id,
    )

    # Verify that currently used public key is available
    if header_claims[claim_key_id] in public_keys:
        key = public_keys[header_claims[claim_key_id]]
    else:
        logger.warning(
            (
                "Used JWT key ID not found among issuer's public keys."
            )
        )
        return {}

    # Decode JWT and validate via public key
    try:
        claims = decode(
            jwt=jwt,
            verify=True,
            key=key,
            algorithms=algorithms
        )
    except Exception as e:
        logger.warning(
            f"JWT could not be decoded. Original error message: " \
            f"{type(e).__name__}: {e}"
        )
        return {}

    return claims


def get_public_keys(
    url: str,
    claim_key_id: str = 'kid',
) -> Mapping:
    """
    Obtain the identity provider's list of public keys.
    """
    # Get JWK sets from identity provider
    try:
        response = requests.get(url)
        response.raise_for_status()
    except Exception as e:
        logger.warning(
            f"Could not connect to endpoint '{url}'. Original error " \
            f"message: {type(e).__name__}: {e}"
        )
        return {}

    # Iterate over all JWK sets and store public keys in dictionary
    public_keys = {}
    for jwk in response.json()['keys']:
        public_keys[jwk[claim_key_id]] = algorithms.RSAAlgorithm.from_jwk(
            json.dumps(jwk)
        )

    # Return dictionary of public keys
    return public_keys
