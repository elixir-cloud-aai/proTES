"""
Unit tests.

Tested function: pro_tes.security.utils.get_idp_service_info_from_jwt_issuer_claim()
"""
from requests.exceptions import (ConnectionError, HTTPError, MissingSchema)

import pytest

from pro_tes.security.utils import (
    get_idp_service_info_from_jwt_issuer_claim
)

# Test parameters
ISSUER_OKAY="https://login.elixir-czech.org/oidc/"
ISSUER_INVALID_BUT_API="https://jsonplaceholder.typicode.com/todos/1"
ISSUER_NO_IDP="https://8.8.8.8/"
ISSUER_NA_URL="https://doesnot.exist"
ISSUER_INVALID_URL="thisisnotaurl"
SUFFIX_OKAY="/.well-known/openid-configuration"
SUFFIX_NONE=""
SUFFIX_INVALID="/some_invalid/suffix"


# Unit tests
def test_valid_issuer():
    ret = get_idp_service_info_from_jwt_issuer_claim(
        issuer=ISSUER_OKAY,
        suffix=SUFFIX_OKAY,
    )
    assert 'userinfo_endpoint' in ret


def test_suffix_absent():
    with pytest.raises(TypeError):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_OKAY,
            suffix=SUFFIX_NONE,
        )


def test_suffix_invalid():
    with pytest.raises(TypeError):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_OKAY,
            suffix=SUFFIX_INVALID,
        )


def test_no_idp_but_valid_api():
    with pytest.raises(KeyError):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_INVALID_BUT_API,
            suffix=SUFFIX_NONE,
        )


def test_no_idp_but_valid_url():
    with pytest.raises(HTTPError):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_NO_IDP,
            suffix=SUFFIX_OKAY,
        )


def test_issuer_url_not_available():
    with pytest.raises(ConnectionError):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_NA_URL,
            suffix=SUFFIX_OKAY,
        )


def test_issuer_url_invalid():
    with pytest.raises(MissingSchema):
        assert get_idp_service_info_from_jwt_issuer_claim(
            issuer=ISSUER_INVALID_URL,
            suffix=SUFFIX_OKAY,
        )
