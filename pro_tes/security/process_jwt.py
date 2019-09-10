"""
Classes and functions for dealing with the processing of JSON Web Tokens (JWTs).
"""
from enum import Enum
from functools import partial
import json
from os import get_inheritable
import requests
from simplejson.errors import JSONDecodeError
from typing import (Dict, List, Union)

from jwt import (decode, get_unverified_header, algorithms)


class JWT:
    """
    Class that extracts JSON Web Tokens (JWT) and related information from a 
    HTTP request and validates the JWT via one or more methods.
    """

    # Class attributes (can be updated through JWT.config(**kwargs))
    auth_header_key: str = "Authorization"
    claim_identity: str = "sub"
    claim_issuer: str = "iss"
    claim_key_id: str = "kid"
    decode_algorithms: List[str] = ["RS256"]
    idp_config_jwks: str = "jwks_uri"
    idp_config_url_suffix: str = "/.well-known/openid-configuration"
    idp_config_userinfo: str = "userinfo_endpoint"
    jwt_prefix: str = "Bearer"
    validation_methods: List[str] = ["user_endpoint", "public_key"]


    # Class methods
    @classmethod
    def config(cls, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(cls, k, v)


    # Constructors
    def __init__(
        self, 
        jwt: Union[None, str] = None,
        request: Union[None, requests.models.Request] = None,
        user: str = "",
        claims: Dict = {},
        header_claims: Dict = {},
        idp_config: Dict = {},
        public_keys: Dict = {},
        current_key: str = "",
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
        # JWT not passed and cannot be extracted
        if jwt is None and request is None:
            raise ValueError(
                "Either a JWT or a request object with a header containg a " \
                "JWT needs to be passed to the constructor."
            )

        # Extract JWT from header
        if jwt is None:

            # Get authorization header
            try:
                auth_header = request.headers.get(self.auth_header_key, None)
            except AttributeError:
                raise AttributeError(
                    "Agument passed to parameter 'request' does not look loke a " \
                    "valid HTTP request."
                )

            if auth_header is None:
                raise KeyError(
                    f"No HTTP header with name '{self.auth_header_key}' found."
                )

            # Ensure that authorization header contains prefix
            try:
                (found_prefix, jwt) = auth_header.split()
            except ValueError:
                raise ValueError(
                    "Authentication header is malformed, prefix and JWT expected."
                )

            # Ensure that prefix is correct
            if found_prefix != self.jwt_prefix:
                raise ValueError(
                    f"Expected JWT prefix '{self.jwt_prefix}' in authentication " \
                    f"header, but found '{found_prefix}' instead."
                )

        # Initialize instance
        self.jwt = jwt
        self.user = user 
        self.claims = claims
        self.header_claims = header_claims
        self.idp_config = idp_config
        self.public_keys = public_keys
        self.current_key = current_key


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
                    algorithms=self.decode_algorithms,
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

            # Get claims unless present
            try:
                self.get_claims(force=force)
            except Exception:
                raise

            # Build endpoint URL
            try:
                root = self.claims[self.claim_issuer].rstrip('/')
            except KeyError as e:
                raise KeyError(
                    f"Issuer '{self.claim_issuer}' is not available. " \
                    f"Original error message: {type(e).__name__}: {e}"
                ) from e
            url = f"{root}/{self.idp_config_url_suffix}"

            # Send GET request to OIDC service info/config endpoint
            try:
                response = requests.get(url)
                response.raise_for_status()
            except requests.exceptions.MissingSchema as e:
                raise requests.exceptions.MissingSchema(
                    f"Value '{url} could not be interpreted as URL."
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

            # Set IdP config
            self.idp_config = response


    def get_public_keys(
        self,
        force: bool = False,
    ) -> None:
        """
        Obtain the identity provider's list of public keys.
        """
        if not self.public_keys or force:

            # Get IdP config
            try:
                self.get_idp_config(force=force)
            except Exception:
                raise

            # Get JWK set URL
            try:
                url = self.idp_config[self.idp_config_jwks]
            except KeyError as e:
                raise KeyError (
                    f"Field '{self.idp_config_jwks}' not available in " \
                    f"identity provider's config. Original error message: " \
                    f"{type(e).__name__}: {e}"
                ) from e
        
            # Get JWK sets from identity provider
            try:
                response = requests.get(url)
                response.raise_for_status()
            except Exception as e:
                raise Exception(
                    f"Could not connect to endpoint '{url}'. Original error " \
                    f"message: {type(e).__name__}: {e}"
                ) from e

            # Iterate over all JWK sets and store public keys
            keys = {}
            try:
                for jwk in response.json()['keys']:
                    keys[jwk[self.claim_key_id]] = algorithms.RSAAlgorithm.\
                        from_jwk(json.dumps(jwk))
            except KeyError as e:
                raise KeyError(
                    f"Public keys could not be processed. Original error " \
                    f"message: {type(e).__name__}: {e}"
                ) from e
            self.public_keys = keys


    def get_current_key(
        self,
        force: bool = False,
    ) -> None:
        """
        
        """
        if not self.current_key or force:

            # Get public keys
            try:
                self.get_public_keys(force=force)
            except Exception:
                raise

            # Get JWT header claims 
            try:
                self.get_header_claims(force=force)
            except Exception:
                raise

            # Get JWT key ID
            try:
                key_id_used = self.header_claims[self.claim_key_id]
            except KeyError as e:
                f"Key ID claim '{self.claim_key_id}' is not available in " \
                f"JWT. Original error message: {type(e).__name__}: {e}"

            # Set JWT public key 
            try:
                self.current_key = self.public_keys[key_id_used]
            except KeyError as e:
                raise KeyError(
                    f"Key used in JWT not available in issuer's JWK sets. " \
                    f"Original error message: {type(e).__name__}: {e}"
                ) from e


    def validate(
        self,
        force: bool = False,
    ) -> None:
        """
        
        """
        if not len(self.validation_methods):
            raise ValueError(
                "No validation methods configured."
            )
        for method in self.validation_methods:
            try:
                ValidationMethods[method].value(self, force=force)
            except Exception as e:
                raise ValueError(
                    f"Validation of JWT '{self.jwt}' by method " \
                    f"'{method}' failed. Original error message: " \
                    f"{type(e).__name__}: {e}"
                ) from e


    def get_user_info(
        self,
        force: bool = False,
    ) -> None:
        """
        
        """
        # Get IdP config
        try:
            self.get_idp_config(force=force)
        except Exception:
            raise

        # Get userinfo URL
        try:
            url = self.idp_config[self.idp_config_userinfo]
        except KeyError as e:
            raise KeyError (
                f"Field '{self.idp_config_userinfo}' not available in " \
                f"identity provider's config. Original error message: " \
                f"{type(e).__name__}: {e}"
            ) from e
        
        # Build headers
        headers = {
            f"{self.auth_header_key}": f"{self.jwt_prefix} {self.jwt}"
        }

        # Get user info
        try:
            response = requests.get(
                url,
                headers=headers,
            )
            response.raise_for_status()
        except Exception:
            raise
        self.user_info = response

    
    def validate_signature(
        self,
        force: bool = False,
        update_claims: bool = False,
    ):
        try:
            self.get_current_key(force=force)
        except Exception:
            raise
        
        try:
            response = decode(
                jwt=self.jwt,
                verify=True,
                key=self.current_key,
                algorithms=self.decode_algorithms,
            )
        except Exception as e:
            raise Exception(
                f"JWT could not be decoded. Original error message: " \
                f"{type(e).__name__}: {e}"
            ) from e
        
        if update_claims:
            self.claims = response


    def get_user(
        self,
        force: bool = False,
    ):
        """
    
        """
        if not self.user or force:

            # Get claims unless present
            try:
                self.get_claims(force=force)
            except Exception:
                raise

            # Get user ID
            try:
                self.user = self.claims[self.claim_identity]
            except KeyError as e:
                f"Key ID claim '{self.claim_identity}' is not available in " \
                f"JWT. Original error message: {type(e).__name__}: {e}"


class ValidationMethods(Enum):
    """Enumerator class for different JSON Web Token validation methods."""
    user_endpoint = partial(JWT.get_user_info)
    public_key = partial(JWT.validate_signature)
