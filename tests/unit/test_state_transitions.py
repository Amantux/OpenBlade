import pytest

from openblade.domain.errors import InvalidStateTransitionError
from openblade.domain.models import CartridgeState, DriveState, MountState
from openblade.domain.states import (
    can_unload_drive,
    validate_cartridge_transition,
    validate_drive_transition,
    validate_mount_transition,
)


def test_valid_cartridge_transition() -> None:
    validate_cartridge_transition(CartridgeState.IN_SLOT, CartridgeState.IN_DRIVE)


def test_invalid_cartridge_transition() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_cartridge_transition(CartridgeState.IN_DRIVE, CartridgeState.EXPORTED)


def test_valid_drive_transition() -> None:
    validate_drive_transition(DriveState.EMPTY, DriveState.LOADED)


def test_invalid_drive_transition() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_drive_transition(DriveState.EMPTY, DriveState.BUSY)


def test_valid_mount_transition() -> None:
    validate_mount_transition(MountState.UNMOUNTED, MountState.MOUNTED_RW)


def test_invalid_mount_transition() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_mount_transition(MountState.DIRTY, MountState.MOUNTED_RW)


@pytest.mark.parametrize(
    ("drive_state", "mount_state", "expected"),
    [
        (DriveState.LOADED, MountState.UNMOUNTED, True),
        (DriveState.LOADED, MountState.MOUNTED_RW, False),
        (DriveState.LOADED, MountState.DIRTY, False),
    ],
)
def test_can_unload_drive(drive_state: DriveState, mount_state: MountState, expected: bool) -> None:
    assert can_unload_drive(drive_state, mount_state) is expected
