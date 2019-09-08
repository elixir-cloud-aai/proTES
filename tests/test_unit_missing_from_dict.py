"""
Unit tests.

Tested function:
`pro_tes.utils.utils.missing_from_dict()'
"""
import pytest

from pro_tes.utils.utils import missing_from_dict

# Test parameters
KEY_1 = "a"
KEY_2 = "b"
KEY_3 = "c"
KEYS = [KEY_1, KEY_2, KEY_3]
MISSING_KEY_1 = "d"
MISSING_KEY_2 = "e"
MISSING_KEYS = [MISSING_KEY_1, MISSING_KEY_2]
MISSING_KEYS_UNUSUAL = [1, (1, 2), None, print]
VALUES = [1, 2, 3]
DICTIONARY = dict(zip(KEYS, VALUES))
NO_DICTIONARY = KEYS


# Unit tests
def test_all_arguments_present():
    ret = missing_from_dict(
        *KEYS,
        dictionary=DICTIONARY,
    )
    assert ret == []


def test_no_dictionary():
    with pytest.raises(AttributeError):
        assert missing_from_dict(
            *KEYS,
            dictionary=NO_DICTIONARY,
        )


def test_one_missing():
    ret = missing_from_dict(
        *KEYS,
        MISSING_KEY_1,
        dictionary=DICTIONARY,
    )
    assert ret == [MISSING_KEY_1]


def test_all_missing():
    ret = missing_from_dict(
        *MISSING_KEYS,
        dictionary=DICTIONARY,
    )
    assert len(ret) == len(MISSING_KEYS)
    assert set(ret) == set(MISSING_KEYS)


def test_accepts_unusual_keys():
    ret = missing_from_dict(
        *MISSING_KEYS_UNUSUAL,
        dictionary=DICTIONARY,
    )
    assert len(ret) == len(MISSING_KEYS_UNUSUAL)
    assert set(ret) == set(MISSING_KEYS_UNUSUAL)
