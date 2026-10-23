"""Tests of the miscellaneous utilities."""

import flask
from foca.config.access import set_app_config
from foca.models.config import Config
import pytest

from pro_tes.utils.misc import get_custom_config, strip_auth


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://tes.example.org/path", "https://tes.example.org/path"),
        (
            "ftp://user:secret@ftp.example.org:21/in?q=1#f",
            "ftp://ftp.example.org:21/in?q=1#f",
        ),
        ("s3://user@bucket/key", "s3://bucket/key"),
        ("https://user:p@ss@host.example.org/", "https://host.example.org/"),
        ("/data/file", "/data/file"),
    ],
)
def test_strip_auth(url: str, expected: str) -> None:
    """User names and passwords are removed from URLs."""
    assert strip_auth(url) == expected


def test_get_custom_config_missing() -> None:
    """Apps without the custom config model are reported."""
    app = flask.Flask(__name__)
    set_app_config(app, Config())
    with (
        app.app_context(),
        pytest.raises(RuntimeError, match="No custom configuration"),
    ):
        get_custom_config()
