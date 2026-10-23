"""TES task states."""

from typing import ClassVar


class States:
    """TES task states, in groups.

    Each state of :class:`~pro_tes.ga4gh.tes.models.TesState` is in exactly
    one of the base groups `UNDEFINED`, `CANCELABLE`, `CANCELING` and
    `FINISHED`; the other groups are unions of these. Tasks are tracked at
    their backend until their state is in `FINISHED`.
    """

    UNDEFINED: ClassVar[list[str]] = [
        "UNKNOWN",
    ]

    CANCELABLE: ClassVar[list[str]] = [
        "INITIALIZING",
        "PAUSED",
        "QUEUED",
        "RUNNING",
    ]

    # canceled, but their resources are not yet deleted (TES 1.1): tracked
    # until they are final, and not cancelable again
    CANCELING: ClassVar[list[str]] = [
        "CANCELING",
    ]

    UNFINISHED: ClassVar[list[str]] = CANCELABLE + CANCELING + UNDEFINED

    FINISHED: ClassVar[list[str]] = [
        "COMPLETE",
        "CANCELED",
        "EXECUTOR_ERROR",
        "SYSTEM_ERROR",
        # stopped by the system (TES 1.1)
        "PREEMPTED",
    ]

    DEFINED: ClassVar[list[str]] = CANCELABLE + CANCELING + FINISHED

    ALL: ClassVar[list[str]] = UNDEFINED + DEFINED
