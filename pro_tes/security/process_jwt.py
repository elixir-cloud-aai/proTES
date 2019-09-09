"""
Classes and functions for dealing with the processing of JSON Web Tokens (JWTs).
"""
from enum import Enum
import requests
from simplejson.errors import JSONDecodeError
from typing import (Callable, Dict, List)

from jwt import (decode, get_unverified_header, algorithms)


class ValidationMethods(Enum):
    """Enumerator class for different JSON Web Token validation methods."""
    user_endpoint = "_validate_by_user_endpoint"
    public_key = "_validate_by_public_key"


class JWT:
    """
    Class that extracts JSON Web Tokens (JWT) and related information from a 
    HTTP request and validates the JWT via one or more methods.
    """

    # Class attributes (can be updated through JWT.config(**kwargs))
    authorization_header_key: str = "Authorization"
    jwt_prefix: str = "Bearer"
    idp_config_url_suffix: str = "/.well-known/openid-configuration"
    decode_algorithms: List[str] = ['RS256']
    claim_identity: str = 'sub'
    claim_issuer: str = 'iss'
    claim_key_id: str = 'kid'
    validation_methods: List[str] = ["user_endpoint", "public_key"]
    validate_by: str = 'any'


    # Class methods
    @classmethod
    def config(cls, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(cls, k, v)


    # Constructors
    def __init__(
        self, 
        jwt: str,
        claims: Dict = {},
        header_claims: Dict = {},
        idp_config: Dict = {},
    ) -> None:
        """

        """
        self.jwt = jwt
        self.claims = claims
        self.header_claims = header_claims
        self.idp_config = idp_config


    def from_request(
        self,
        request: requests.models.Request,
        header_key: str = authorization_header_key,
        prefix: str = jwt_prefix,
    ) -> None:
        """
#        Constructs JWT class instance by parsing the JWT from a HTTP request 
#        header.
#
#        :param request: HTTP request. Instance of `requests.models.Request`.
#        :param header_key: Key/name of header item that contains the JWT.
#        :param prefix: Prefix separated from JWT by whitespace, e.g., "Bearer".
#
#        :return: JWT.
#
#        :raises AttributeError: Argument to `request` is not of the expected
#                type.
#        :raises KeyError: No header with specified name available.
#        :raises ValueError: Value of authentication header does not contain
#                prefix.
#        :raises ValueError: Value of authentication header has wrong prefix.
        """
        # Get authorization header
        try:
            auth_header = request.headers.get(header_key, None)
        except AttributeError:
            raise AttributeError(
                "Agument passed to parameter 'request' does not look loke a " \
                "valid HTTP request."
            )

        if auth_header is None:
            raise KeyError(f"No HTTP header with name '{header_key}' found.")

        # Ensure that authorization header contains prefix
        try:
            (found_prefix, jwt) = auth_header.split()
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

        # Create object instance
        self.__init__(jwt=jwt)


    # Other methods
    def get_claims(
        self,
        force: bool=False,
    ) -> None:
        """
#
        """
        if not self.claims or force:
            try:
                self.claims = decode(
                    jwt=self.jwt,
                    verify=False,
                    algorithms=algorithms,
                )
            except Exception as e:
                raise Exception(
                    f"JWT could not be decoded. Original error message: " \
                    f"{type(e).__name__}: {e}"
                ) from e


    def get_header_claims(
        self,
        force: bool=False,
    ) -> None:
        """
#
        """
        if not self.header_claims or force:
            try:
                self.header_claims = get_unverified_header(self.jwt)
            except Exception as e:
                raise Exception(
                    f"Could not extract JWT header claims. Original error " \
                    f"message: {type(e).__name__}: {e}"
                ) from e


    def get_idp_config(
        self,
        force: bool = False,
        issuer: str = claim_issuer,
        suffix: str = idp_config_url_suffix,
    ) -> None:
        """
#        Retrieves an OpenID Connect (OIDC) identity provider's (IdP) service info 
#        based on a JSON Web Token's (JWT) issuer claim.
#
#        :param issuer: JWT issuer claim and base URL for service info endpoint.
#        :param suffix: URL suffix for IdP service info endpoint.
#
#        :returns: Dictionary of IdP service info/configuration.
#
#        :raises KeyError: Response is valid JSON but does not contain information
#                required by OIDC standard.
#        :raises requests.exceptions.ConnectionError: Not very well defined.
#        :raises requests.exceptions.HTTPError: Not very well defined.
#        :raises requests.exceptions.MissingSchema: Compiled URL cannot be
#                interpreted as URL.
#        :raises TypeError: Response is not valid JSON.
        """
        if not self.idp_config or force:

            # Ensure that claims are present
            if not self.claims:
                self.get_claims()

            # Build endpoint URL
            try:
                root = self.claims[issuer].rstrip('/')
            except KeyError as e:
                raise KeyError(
                    f"Issuer '{issuer}' is not available. Original " \
                    f"error message: {type(e).__name__}: {e}"
                ) from e
            url = f"{root}/{suffix}"

            # Send GET request to OIDC service info/config endpoint
            try:
                response = requests.get(url)
                response.raise_for_status()
            except requests.exceptions.MissingSchema:
                raise requests.exceptions.MissingSchema(
                    f"Value '{url}' compiled from arguments to '{issuer}' " \
                    f"and '{suffix}' could not be interpreted as a URL."
                ) from e
            except requests.exceptions.ConnectionError:
                raise
            except requests.exceptions.HTTPError:
                raise

            # Convert JSON response to dictionary
            try:
                response = response.json()
            except JSONDecodeError as e:
                raise TypeError(
                    "The response does not look like valid JSON."
                ) from e

            # Simple sanity check
            if not 'issuer' in response:
                raise KeyError(
                    "The response does not look like an OIDC service documentation."
                )

            # Set IdP config
            self.idp_config = response


    def get_public_keys(self) -> None:
        pass


    def get_current_key(self) -> None:
        pass


    def validate(self):
        pass


    def _validate_by_user_endpoint(self):
        pass

    
    def _validate_by_public_key(self):
        pass


