from pathlib import Path

import pytest

from openblade.domain.models import (
    Barcode,
    ChangerState,
    DriveState,
    DriveStatus,
    LibraryInventory,
    MountHandle,
    MountMode,
    MountState,
    OperationResult,
    SlotState,
)


def test_barcode_validation_accepts_valid_code() -> None:
    barcode = Barcode("pho001l8")
    assert str(barcode) == "PHO001L8"


@pytest.mark.parametrize("value", ["SHORT", "toolong001", "bad-0001", "withspace"])
def test_barcode_validation_rejects_invalid_codes(value: str) -> None:
    with pytest.raises(ValueError):
        Barcode(value)


def test_slot_state_creation() -> None:
    slot = SlotState(slot_id=1, barcode=Barcode("PHO001L8"), occupied=True)
    assert slot.occupied is True
    assert str(slot.barcode) == "PHO001L8"


def test_library_inventory_creation() -> None:
    inventory = LibraryInventory(
        library_id="lib-1",
        slots=[SlotState(slot_id=1, barcode=Barcode("PHO001L8"), occupied=True)],
        drives=[
            DriveStatus(
                drive_id=0,
                barcode=None,
                drive_state=DriveState.EMPTY,
                mount_state=MountState.UNMOUNTED,
            )
        ],
        changer_state=ChangerState.IDLE,
    )
    assert inventory.library_id == "lib-1"
    assert inventory.drives[0].drive_state is DriveState.EMPTY


def test_mount_handle_creation() -> None:
    handle = MountHandle(
        barcode=Barcode("PHO001L8"),
        drive_id=0,
        mode=MountMode.READ_ONLY,
        mount_path=Path("mount"),
    )
    assert handle.drive_id == 0
    assert handle.mount_path == Path("mount")


def test_operation_result_success_failure() -> None:
    success = OperationResult(success=True, message="ok")
    failure = OperationResult(success=False, message="nope", details={"reason": "test"})
    assert success.success is True
    assert failure.success is False
    assert failure.details["reason"] == "test"
