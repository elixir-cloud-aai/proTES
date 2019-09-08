"""
Unit tests.

Tested function:
`pro_tes.security.utils.parse_jwt_from_request()`
"""
import requests

import pytest

from pro_tes.security.decorators import parse_jwt_from_request

# Test parameters
URL = "https://8.8.8.8"
JWT_VALUE = "somefakeJWT123456"
HEADER_NAME = "Authorization"
HEADER_NAME_WRONG = "WrongHeaderName"
PREFIX = "Bearer"
PREFIX_WRONG = "WrongPrefix"
JWT_OKAY = ' '.join([PREFIX, JWT_VALUE])
JWT_WRONG_PREFIX = ' '.join([PREFIX_WRONG, JWT_VALUE])
HEADER_OKAY = {HEADER_NAME: JWT_OKAY}
HEADER_WRONG_NAME = {HEADER_NAME_WRONG: JWT_OKAY}
HEADER_WRONG_PREFIX = {HEADER_NAME: JWT_WRONG_PREFIX}
REQUEST_OKAY = requests.Request(URL, headers=HEADER_OKAY)
REQUEST_NO_REQUEST = JWT_VALUE
REQUEST_NO_HEADER = requests.Request(URL)
REQUEST_WRONG_HEADER_NAME = requests.Request(URL, headers=HEADER_WRONG_NAME)
REQUEST_WRONG_JWT_PREFIX = requests.Request(URL, headers=HEADER_WRONG_PREFIX)


# Unit tests
def test_request_okay():
    ret = parse_jwt_from_request(
        request=REQUEST_OKAY,
        header_name=HEADER_NAME,
        prefix=PREFIX
    )
    assert ret == JWT_VALUE

def test_no_request():
    with pytest.raises(AttributeError):
        assert parse_jwt_from_request(
            request=REQUEST_NO_REQUEST,
            header_name=HEADER_NAME,
            prefix=PREFIX
        )

def test_no_header():
    with pytest.raises(KeyError):
        assert parse_jwt_from_request(
            request=REQUEST_NO_HEADER,
            header_name=HEADER_NAME,
            prefix=PREFIX
        )

def test_wrong_header_name():
    with pytest.raises(KeyError):
        assert parse_jwt_from_request(
            request=REQUEST_WRONG_HEADER_NAME,
            header_name=HEADER_NAME,
            prefix=PREFIX
        )

def test_wrong_prefix():
    with pytest.raises(ValueError):
        assert parse_jwt_from_request(
            request=REQUEST_WRONG_JWT_PREFIX,
            header_name=HEADER_NAME,
            prefix=PREFIX
        )
