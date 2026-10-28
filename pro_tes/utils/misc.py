"""Miscellaneous utilities."""

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit


def has_credentials(url: str) -> bool:
    """Check whether a URL has credentials (user information).

    Args:
        url: URL.

    Returns:
        Whether the URL has a user name or password (also empty ones, as in
        `https://@host/path`).

    Raises:
        ValueError: The URL is malformed.
    """
    parts = urlsplit(url)
    return parts.username is not None or parts.password is not None


def strip_auth(url: str) -> str:
    """Remove basic authentication information from URI, if present.

    Expected URI format: scheme://user:password@host:port/path?query#fragment
    After removal: scheme://host:port/path?query#fragment

    Args:
        url: URI.

    Returns:
        URI without basic auth.
    """
    elements = list(urlsplit(url))
    elements[1] = elements[1][elements[1].rfind("@") + 1 :]
    return urlunsplit(elements)


def utc_now() -> str:
    """Get the current time in UTC, in RFC 3339 format, as TES requires.

    Returns:
        The current time, e.g., `2026-10-22T12:34:56.789012+00:00`.
    """
    return datetime.now(UTC).isoformat()
