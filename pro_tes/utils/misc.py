"""Miscellaneous utilities."""

from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from foca import get_config

from pro_tes.config_models import CustomConfig


def get_custom_config() -> CustomConfig:
    """Get the custom configuration of the current app.

    Returns:
        Custom configuration.

    Raises:
        RuntimeError: The app has no custom configuration.
    """
    # FOCA keeps the custom configuration as an untyped extra field; it is an
    # instance of `CustomConfig` only if the app loads the model by its full
    # path (`pro_tes.config_models.CustomConfig`)
    custom = (get_config().model_extra or {}).get("custom")
    if not isinstance(custom, CustomConfig):
        raise RuntimeError(
            f"No custom configuration of type {CustomConfig}; check that "
            "`custom_config_model` is its full module path"
        )
    return custom


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
