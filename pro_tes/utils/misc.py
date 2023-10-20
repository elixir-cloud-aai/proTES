"""Miscellaneous utilities."""

from urllib.parse import urlsplit, urlunsplit


def strip_auth(url: str) -> str:
    """Remove basic authentication information from URI, if present.

    Expected URI format: scheme://user:password@host:port/path?query#fragment
    After removal: scheme://host:port/path?query#fragment

    Args:
        uri: URI.

    Returns:
        URI without basic auth.
    """
    elements = list(urlsplit(url))
    elements[1] = elements[1][elements[1].rfind("@") + 1 :]  # noqa: E203
    return urlunsplit(elements)
