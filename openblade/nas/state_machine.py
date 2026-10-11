"""Tape/cartridge lifecycle state machine for the tape-native NAS.

This models the *cartridge* lifecycle. Per-file states live in
``openblade.nas.types.NasFileState`` and are deliberately not duplicated here:
a file is ``OFFLINE_ON_TAPE``/``MISSING_TAPE``/``EXPORTED`` as a consequence of
the cartridge state it sits on (see :func:`file_state_for_tape`).
"""

from __future__ import annotations

from enum import Enum

from openblade.nas.types import NasFileState


class TapeState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    HYDRATING = "hydrating"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    SEQUESTERED = "sequestered"
    REPAIR_REQUIRED = "repair_required"


class TapeEvent(str, Enum):
    GO_OFFLINE = "go_offline"
    BRING_ONLINE = "bring_online"
    ERROR_DETECTED = "error_detected"
    HYDRATE_START = "hydrate_start"
    HYDRATE_DONE = "hydrate_done"
    EXPORT_START = "export_start"
    EXPORT_DONE = "export_done"
    EXPORT_CANCEL = "export_cancel"
    IMPORT = "import"
    SEQUESTER = "sequester"
    OPERATOR_REPAIR = "operator_repair"
    OPERATOR_RELEASE = "operator_release"
    REPAIR_DONE = "repair_done"


class InvalidTransitionError(ValueError):
    """Raised when an event is not allowed from the current tape state."""

    def __init__(self, state: TapeState, event: TapeEvent) -> None:
        super().__init__(f"transition {event.value!r} not allowed from {state.value!r}")
        self.state = state
        self.event = event


_S = TapeState
_E = TapeEvent

# Explicit, exhaustive transition table. Anything absent is forbidden.
TRANSITIONS: dict[tuple[TapeState, TapeEvent], TapeState] = {
    (_S.ONLINE, _E.GO_OFFLINE): _S.OFFLINE,
    (_S.ONLINE, _E.ERROR_DETECTED): _S.DEGRADED,
    (_S.ONLINE, _E.HYDRATE_START): _S.HYDRATING,
    (_S.ONLINE, _E.EXPORT_START): _S.EXPORTING,
    (_S.ONLINE, _E.SEQUESTER): _S.SEQUESTERED,
    (_S.OFFLINE, _E.BRING_ONLINE): _S.ONLINE,
    (_S.OFFLINE, _E.SEQUESTER): _S.SEQUESTERED,
    (_S.DEGRADED, _E.OPERATOR_REPAIR): _S.REPAIR_REQUIRED,
    (_S.DEGRADED, _E.SEQUESTER): _S.SEQUESTERED,
    (_S.DEGRADED, _E.GO_OFFLINE): _S.OFFLINE,
    (_S.HYDRATING, _E.HYDRATE_DONE): _S.ONLINE,
    (_S.HYDRATING, _E.ERROR_DETECTED): _S.DEGRADED,
    (_S.EXPORTING, _E.EXPORT_DONE): _S.EXPORTED,
    (_S.EXPORTING, _E.EXPORT_CANCEL): _S.ONLINE,
    (_S.EXPORTED, _E.IMPORT): _S.OFFLINE,
    # Sequestered media only leaves quarantine through an explicit operator action.
    (_S.SEQUESTERED, _E.OPERATOR_REPAIR): _S.REPAIR_REQUIRED,
    (_S.SEQUESTERED, _E.OPERATOR_RELEASE): _S.ONLINE,
    (_S.REPAIR_REQUIRED, _E.REPAIR_DONE): _S.ONLINE,
    (_S.REPAIR_REQUIRED, _E.SEQUESTER): _S.SEQUESTERED,
}


def transition(state: TapeState, event: TapeEvent) -> TapeState:
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise InvalidTransitionError(state, event) from None


def file_state_for_tape(state: TapeState) -> NasFileState:
    """Planning view of a file whose only copy is on a tape in ``state``."""
    if state is TapeState.EXPORTED:
        return NasFileState.EXPORTED
    if state is TapeState.HYDRATING:
        return NasFileState.HYDRATING
    if state in (TapeState.SEQUESTERED, TapeState.REPAIR_REQUIRED):
        return NasFileState.MISSING_TAPE
    return NasFileState.OFFLINE_ON_TAPE
