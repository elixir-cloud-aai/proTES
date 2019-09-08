"""Security-related utitity functions."""
import requests
from simplejson.errors import JSONDecodeError
from typing import Dict


def parse_jwt_from_request(
    request: requests.models.Request,
    header_name: str ='Authorization',
    prefix: str ='Bearer'
) -> str:
    """
    Parses JSON Web Token (JWT) from HTTP request header.
    
    :param request: HTTP request. Instance of `requests.models.Request`.
    :param header_name: Key/name of header item that contains the JWT.
    :param prefix: Prefix separated from JWT by whitespace, e.g., "Bearer".

    :return: JWT string.

    :raises AttributeError: Argument to `request` is not of the expected type.
    :raises KeyError: No header with specified name available.
    :raises ValueError: Value of authentication header does not contain prefix.
    :raises ValueError: Value of authentication header has wrong prefix.
    """
    # Get authorization header
    try:
        auth_header = request.headers.get(header_name, None)
    except AttributeError:
        raise AttributeError(
            "Agument passed to parameter 'request' does not look loke a " \
            "valid HTTP request."
        )
    
    if auth_header is None:
        raise KeyError(f"No HTTP header with name '{header_name}' found.")

    # Ensure that authorization header contains prefix
    try:
        (found_prefix, token) = auth_header.split()
    except ValueError:
        raise ValueError(
            "Authentication header is malformed, prefix and JWT expected."
        )

    # Ensure that prefix is correct
    if found_prefix != prefix:
        raise ValueError(
            f"Expected JWT prefix '{prefix}' in authentication header, but " \
            f"found '{found_prefix}' instead."
        )

    # Return token
    return token


def check_get_request_with_jwt_header(
    url: str,
    jwt: str,
    header_name: str = 'Authorizrequests.exceptions.ConnectionErroration',
    prefix: str = 'Bearer'
) -> None:
    """
    Checks whether a GET request with a JSON Web Token in the header sent to 
    the specified endpoint yields a 200 response.

    :param url: URL to which the GET request will be sent.
    :param jwt: JSON web token value.
    :param header_name: Key/name of header item that will contain the JWT.
    :param prefix: Prefix separated from JWT by whitespace, e.g., "Bearer".

    :returns: None

    :raises Exception:
    """
    headers = {f"{header_name}": f"{prefix} {jwt}"}
    try:
        response = requests.get(
            url,
            headers=headers,
        )
        response.raise_for_status()
    except Exception:
        raise
    return None


def get_idp_service_info_from_jwt_issuer_claim(
        issuer: str,
        suffix: str = '/.well-known/openid-configuration',
    ) -> Dict:
    """
    Retrieves an OpenID Connect (OIDC) identity provider's (IdP) service info 
    based on a JSON Web Token's (JWT) issuer claim.

    :param issuer: JWT issuer claim and base URL for service info endpoint.
    :param suffix: URL suffix for IdP service info endpoint.

    :returns: Dictionary of IdP service info/configuration.

    :raises KeyError: Response is valid JSON but does not contain information
            required by OIDC standard.
    :raises requests.exceptions.ConnectionError: Not very well defined.
    :raises requests.exceptions.HTTPError: Not very well defined.
    :raises requests.exceptions.MissingSchema: Compiled URL cannot be
            interpreted as URL.
    :raises TypeError: Response is not valid JSON.
    """
    # Build endpoint URL
    url = f"{issuer.rstrip('/')}/{suffix}"

    # Send GET request to OIDC service info/config endpoint
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.MissingSchema:
        raise requests.exceptions.MissingSchema(
            f"Value '{url}' compiled from arguments to '{issuer}' and " \
            f"'{suffix}' could not be interpreted as a URL."
        )
    except requests.exceptions.ConnectionError:
        raise
    except requests.exceptions.HTTPError:
        raise

    # Convert JSON response to dictionary
    try:
        response = response.json()
    except JSONDecodeError:
        raise TypeError("The response does not look like valid JSON.")

    # Simple sanity check
    if not 'issuer' in response:
        raise KeyError(
            "The response does not look like an OIDC service documentation."
        )
    
    # Return config dictionary
    return response
