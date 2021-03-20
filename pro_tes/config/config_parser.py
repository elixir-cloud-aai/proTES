import logging
from typing import (Any, List, Mapping)


# Get logger instance
logger = logging.getLogger(__name__)

def get_conf_type(
    config: Mapping[Any, Any],
    *args: str,
    types: Any = False,
    invert_types: bool = False,
    touchy: bool = True
):
    """Returns the value corresponding to a chain of keys from a nested
    dictionary.

    Args:
        config: Dictionary containing config values.
        *args: Keys of nested dictionary, from outer to innermost.
        types: Tuple of allowed types for return values; if `False`, no
               checking is done.
        invert_types: Types specified in parameter `types` are *forbidden*;
                      ignored if `types` is `False`.
        touchy: If `True`, exceptions will raise `SystemExit(1)`; otherwise
                exceptions are re-raised.

    Raises:
        AttributeError: May occur when an illegal value is provided for
                        `*args`; raised only if `touchy` is `False`.
        KeyError: Raised when dictionary keys passed in `*args` are not
                  available and `touchy` is `False`.
        TypeError: The return value is not of any of the allowed `types` or
                   is among any of the forbidden `types` (if `invert_types` is
                   `True`); only raised if `touchy` is `False`.
        SystemExit: Raised if any exception occurs and `touchy` is `True`.
    """
    # Get value for list of keys
    keys = list(args)
    try:
        val = config[keys.pop(0)]
        while keys:
            val = val[keys.pop(0)]

        # Check type
        if types:
            if not invert_types:
                if not isinstance(val, types):
                    raise TypeError(
                        (
                            "Value '{val}' expected to be of type '{types}', "
                            "but is of type '{type}'."
                        ).format(
                            val=val,
                            types=types,
                            type=type(val),
                        )
                    )
            else:
                if isinstance(val, types):
                    raise TypeError(
                        (
                            "Type '{type}' of value '{val}' is forbidden."
                        ).format(
                            type=type(val),
                            val=val,
                        )
                    )

    except (AttributeError, KeyError, TypeError, ValueError) as e:

        if touchy:
            logger.exception(
                (
                    'Config file corrupt. Execution aborted. Original error '
                    'message: {type}: {msg}'
                ).format(
                    type=type(e).__name__,
                    msg=e,
                )
            )
            raise SystemExit(1)

        else:
            raise

    else:
        return val


def get_conf(
    config: Mapping[str, Any],
    *args: str,
    touchy: bool = True
):
    """Returns the value corresponding to a chain of keys from a nested
    dictionary. Extracts only 'leafs' of nested dictionary.

    Shortcut for ```
    get_conf_type(
        config,
        *args,
        types=(dict, list),
        invert_types=True
    ```

    See signature for `get_conf_type()` for info on arguments and errors.
    """
    return get_conf_type(
        config,
        *args,
        types=(dict, list),
        invert_types=True,
        touchy=touchy,
    )