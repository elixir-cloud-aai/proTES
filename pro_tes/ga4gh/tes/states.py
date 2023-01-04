"""TES task states."""

# pragma pylint: disable=too-few-public-methods


class States:
    """TES task states."""

    UNDEFINED = [
        "UNKNOWN",
    ]

    CANCELABLE = [
        "INITIALIZING",
        "PAUSED",
        "QUEUED",
        "RUNNING",
    ]

    UNFINISHED = CANCELABLE + UNDEFINED

    FINISHED = [
        "COMPLETE",
        "CANCELED",
        "EXECUTOR_ERROR",
        "SYSTEM_ERROR",
    ]

    DEFINED = UNFINISHED + FINISHED

    ALL = UNDEFINED + DEFINED
