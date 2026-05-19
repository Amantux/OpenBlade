"""State transition validation for domain objects."""

from typing import TypeVar

from openblade.domain.errors import InvalidStateTransitionError
from openblade.domain.models import CartridgeState, DriveState, MountState

CARTRIDGE_TRANSITIONS: dict[CartridgeState, set[CartridgeState]] = {
    CartridgeState.IN_SLOT: {CartridgeState.IN_DRIVE, CartridgeState.EXPORTED},
    CartridgeState.IN_DRIVE: {CartridgeState.IN_SLOT},
    CartridgeState.EXPORTED: {CartridgeState.IN_SLOT},
    CartridgeState.MISSING: {CartridgeState.IN_SLOT},
    CartridgeState.CLEANING: {CartridgeState.IN_SLOT, CartridgeState.IN_DRIVE},
}

DRIVE_TRANSITIONS: dict[DriveState, set[DriveState]] = {
    DriveState.EMPTY: {DriveState.LOADED},
    DriveState.LOADED: {DriveState.EMPTY, DriveState.BUSY, DriveState.FAILED},
    DriveState.BUSY: {DriveState.LOADED, DriveState.FAILED},
    DriveState.FAILED: {DriveState.EMPTY},
}

MOUNT_TRANSITIONS: dict[MountState, set[MountState]] = {
    MountState.UNMOUNTED: {MountState.MOUNTED_RO, MountState.MOUNTED_RW},
    MountState.MOUNTED_RO: {MountState.UNMOUNTED},
    MountState.MOUNTED_RW: {MountState.UNMOUNTED, MountState.DIRTY},
    MountState.DIRTY: {MountState.UNMOUNTED},
}

StateT = TypeVar("StateT")


def _validate_transition(
    transitions: dict[StateT, set[StateT]], from_state: StateT, to_state: StateT, name: str
) -> None:
    if from_state == to_state:
        return
    valid = transitions.get(from_state, set())
    if to_state not in valid:
        raise InvalidStateTransitionError(f"Invalid {name} transition: {from_state} -> {to_state}")


def validate_cartridge_transition(from_state: CartridgeState, to_state: CartridgeState) -> None:
    """Raise InvalidStateTransitionError if the transition is invalid."""
    _validate_transition(CARTRIDGE_TRANSITIONS, from_state, to_state, "cartridge")


def validate_drive_transition(from_state: DriveState, to_state: DriveState) -> None:
    _validate_transition(DRIVE_TRANSITIONS, from_state, to_state, "drive")


def validate_mount_transition(from_state: MountState, to_state: MountState) -> None:
    _validate_transition(MOUNT_TRANSITIONS, from_state, to_state, "mount")


def can_unload_drive(drive_state: DriveState, mount_state: MountState) -> bool:
    """Return True only if it is safe to unload the drive."""
    return mount_state == MountState.UNMOUNTED and drive_state in {
        DriveState.LOADED,
        DriveState.FAILED,
    }
