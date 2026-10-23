"""Tests of the groups of TES task states."""

from collections import Counter

from pro_tes.ga4gh.tes.models import TesState
from pro_tes.ga4gh.tes.states import States

# Groups that each state of the model is in exactly one of
BASE_GROUPS: dict[str, list[str]] = {
    "UNDEFINED": States.UNDEFINED,
    "CANCELABLE": States.CANCELABLE,
    "CANCELING": States.CANCELING,
    "FINISHED": States.FINISHED,
}
MODEL_STATES: Counter[str] = Counter(state.value for state in TesState)


def test_states_in_one_base_group() -> None:
    """Each state of the model is in exactly one base group.

    States that are in no group would be polled forever by the tracker (as
    `PREEMPTED` was).
    """
    grouped = Counter(state for group in BASE_GROUPS.values() for state in group)
    assert grouped == MODEL_STATES


def test_finished_or_unfinished() -> None:
    """Each state of the model is either finished or unfinished."""
    assert Counter(States.UNFINISHED + States.FINISHED) == MODEL_STATES
    assert Counter(States.ALL) == MODEL_STATES


def test_defined() -> None:
    """All states but `UNKNOWN` are defined."""
    assert Counter(States.DEFINED + States.UNDEFINED) == MODEL_STATES
    assert [TesState.UNKNOWN.value] == States.UNDEFINED


def test_tes_1_1_states() -> None:
    """`PREEMPTED` is final; `CANCELING` is unfinished, but not cancelable.

    Cf. TES 1.1: a preempted task "is stopped (preempted) by the system"; the
    resources of a canceling task "are still awaiting deletion".
    """
    assert TesState.PREEMPTED.value in States.FINISHED
    assert TesState.CANCELING.value in States.UNFINISHED
    assert TesState.CANCELING.value not in States.CANCELABLE
