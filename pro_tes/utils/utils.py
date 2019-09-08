"""General purpose utitlity functions."""
from typing import (Any, Dict, List)


def missing_from_dict(
    *args: Any,
    dictionary: Dict,
) -> List:
    """
    Validates the existence of dictionary keys. Returns a list of arguments
    that were _NOT_ found in the dictionary.

    :param *args: The existence of each positional argument as keys in
            dictionary `dictionary` will be verified.
    :param dictionary: The dictionary in which the positional arguments `*args`
            will be searched for.
    
    :return: A list of those positional arguments in `*args` that are not
            available as keys in `dictionary`.
    """
    try:
        return list(set(args).difference(dictionary.keys()))
    except AttributeError:
        raise AttributeError(
            (
                "Argument passed to parameter 'dictionary' does not look like "
                "a valid dictionary."
            )
        )
    except Exception:
        raise
