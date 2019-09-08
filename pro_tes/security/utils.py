"""Security-related utitity functions."""

import requests


def parse_jwt_from_request(
    request: requests.models.Request,
    header_name: str ='Authorization',
    prefix: str ='Bearer'
) -> str:
    """Parses Json Web Token (JWT) from HTTP request header.
    
    :param request: HTTP request. Instance of `requests.models.Request`.
    :param header_name: Key/name of header item that contains the JWT.
    :param prefix: Prefix separated from JWT by whitespace, e.g., "Bearer".

    :return: JWT string.
    """
    # Get authorization header
    try:
        auth_header = request.headers.get(header_name, None)
    except AttributeError:
        raise AttributeError(
            (
                "Agument passed to parameter 'request' does not look loke a "
                "valid HTTP request."
            )
        )
    except Exception:
        raise

    # Ensure that authorization header is present
    if not auth_header:
        raise KeyError(
            "No HTTP header with name '{header_name}' found.".format(
                header_name=header_name,
            )
        )

    # Ensure that authorization header contains prefix
    try:
        (found_prefix, token) = auth_header.split()
    except ValueError:
        raise ValueError(
            "Authentication header is malformed, prefix and JWT expected."
        )
    except Exception:
        raise

    # Ensure that prefix is correct
    if found_prefix != prefix:
        raise ValueError(
            (
                "Expected token prefix in authentication header is '{prefix}', "
                "but '{found_prefix}' was found."
            ).format(
                prefix=prefix,
                found_prefix=found_prefix,
            )
        )

    # Return token
    return token

