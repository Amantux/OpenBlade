from __future__ import annotations

import itertools

import pytest

from openblade.nas.state_machine import (
    TRANSITIONS,
    InvalidTransitionError,
    TapeEvent,
    TapeState,
    file_state_for_tape,
    transition,
)
from openblade.nas.types import NasFileState

S = TapeState
E = TapeEvent


@pytest.mark.parametrize(("pair", "target"), list(TRANSITIONS.items()))
def test_every_allowed_transition(pair: tuple[TapeState, TapeEvent], target: TapeState) -> None:
    assert transition(*pair) is target


_FORBIDDEN = [p for p in itertools.product(S, E) if p not in TRANSITIONS]


@pytest.mark.parametrize(("state", "event"), _FORBIDDEN)
def test_every_forbidden_transition_raises_typed(state: TapeState, event: TapeEvent) -> None:
    with pytest.raises(InvalidTransitionError) as exc:
        transition(state, event)
    assert exc.value.state is state and exc.value.event is event


def test_exported_cannot_come_online_without_import() -> None:
    with pytest.raises(InvalidTransitionError):
        transition(S.EXPORTED, E.BRING_ONLINE)
    assert transition(transition(S.EXPORTED, E.IMPORT), E.BRING_ONLINE) is S.ONLINE


def test_exporting_cannot_jump_online_except_by_cancel() -> None:
    for event in E:
        if event in (E.EXPORT_DONE, E.EXPORT_CANCEL):
            continue
        with pytest.raises(InvalidTransitionError):
            transition(S.EXPORTING, event)


def test_sequestered_leaves_only_via_operator() -> None:
    exits = {e for (s, e) in TRANSITIONS if s is S.SEQUESTERED}
    assert exits == {E.OPERATOR_REPAIR, E.OPERATOR_RELEASE}
    assert {TRANSITIONS[(S.SEQUESTERED, e)] for e in exits} == {S.REPAIR_REQUIRED, S.ONLINE}


def test_degraded_never_returns_online_without_repair() -> None:
    assert (S.DEGRADED, E.BRING_ONLINE) not in TRANSITIONS
    assert (S.DEGRADED, E.REPAIR_DONE) not in TRANSITIONS


def test_file_state_projection_reuses_nas_file_state() -> None:
    assert file_state_for_tape(S.EXPORTED) is NasFileState.EXPORTED
    assert file_state_for_tape(S.SEQUESTERED) is NasFileState.MISSING_TAPE
    assert file_state_for_tape(S.REPAIR_REQUIRED) is NasFileState.MISSING_TAPE
    assert file_state_for_tape(S.HYDRATING) is NasFileState.HYDRATING
    assert file_state_for_tape(S.OFFLINE) is NasFileState.OFFLINE_ON_TAPE
