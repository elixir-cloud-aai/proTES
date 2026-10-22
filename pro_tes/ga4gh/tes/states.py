"""TES task states."""

from typing import ClassVar


class States:
    """TES task states."""

    UNDEFINED: ClassVar[list[str]] = [
        "UNKNOWN",
    ]

    CANCELABLE: ClassVar[list[str]] = [
        "INITIALIZING",
        "PAUSED",
        "QUEUED",
        "RUNNING",
    ]

    UNFINISHED: ClassVar[list[str]] = CANCELABLE + UNDEFINED

    FINISHED: ClassVar[list[str]] = [
        "COMPLETE",
        "CANCELED",
        "EXECUTOR_ERROR",
        "SYSTEM_ERROR",
    ]

    DEFINED: ClassVar[list[str]] = UNFINISHED + FINISHED

    ALL: ClassVar[list[str]] = UNDEFINED + DEFINED
