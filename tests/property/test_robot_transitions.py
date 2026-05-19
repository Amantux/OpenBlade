from __future__ import annotations

from pathlib import Path, PurePosixPath

from hypothesis import given, settings
from hypothesis import strategies as st

from openblade.domain.errors import DriveOccupiedError, SlotEmptyError, SlotOccupiedError
from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.simulator.scenarios import two_drive_library


@given(st.lists(st.integers(min_value=1, max_value=20), min_size=0, max_size=50))
@settings(max_examples=50)
def test_cartridge_always_in_one_location(operations: list[int]) -> None:
    """A cartridge is always in exactly one location."""
    library, _ = two_drive_library()
    for operation in operations:
        try:
            if (
                operation % 4 == 0
                and library.get_slot(1).occupied
                and library.get_drive(0).barcode is None
            ):
                library.load(1, 0)
            elif (
                operation % 4 == 1
                and library.get_drive(0).barcode is not None
                and not library.get_slot(10).occupied
            ):
                library.unload(0, 10)
            elif (
                operation % 4 == 2
                and library.get_slot(2).occupied
                and library.get_drive(1).barcode is None
            ):
                library.load(2, 1)
            elif (
                operation % 4 == 3
                and library.get_slot(3).occupied
                and not library.get_slot(12).occupied
            ):
                library.move(3, 12)
        except (DriveOccupiedError, SlotEmptyError, SlotOccupiedError):
            pass

        seen: set[str] = set()
        inventory = library.inventory()
        for slot in inventory.slots:
            if slot.barcode is not None:
                assert str(slot.barcode) not in seen
                seen.add(str(slot.barcode))
        for drive in inventory.drives:
            if drive.barcode is not None:
                assert str(drive.barcode) not in seen
                seen.add(str(drive.barcode))


@given(st.binary(min_size=0, max_size=64))
@settings(max_examples=25)
def test_capacity_never_negative(payload: bytes) -> None:
    """Tape used_bytes never goes below zero."""
    library, ltfs = two_drive_library()
    barcode = str(library.get_slot(1).barcode)
    library.load(1, 0)
    ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    path = Path("property-capacity.bin")
    try:
        path.write_bytes(payload)
        ltfs.write_file(handle, path, PurePosixPath("/payload.bin"))
        assert ltfs.ensure_tape(barcode).used_bytes >= 0
        assert ltfs.remaining_capacity(barcode) >= 0
    finally:
        if path.exists():
            path.unlink()


def test_load_unload_preserves_cartridge_count() -> None:
    """Load/unload cycle always results in same number of cartridges."""
    library, _ = two_drive_library()
    initial_count = len(library.get_all_barcodes())

    library.load(1, 0)
    library.unload(0, 15)

    occupied_slots = sum(1 for slot in library.inventory().slots if slot.occupied)
    occupied_drives = sum(1 for drive in library.inventory().drives if drive.barcode is not None)
    assert occupied_slots + occupied_drives == initial_count


def test_no_duplicate_cartridge_locations() -> None:
    """No cartridge appears in both a slot and a drive."""
    library, _ = two_drive_library()
    library.load(1, 0)
    library.load(2, 1)
    inventory = library.inventory()

    slot_barcodes = {str(slot.barcode) for slot in inventory.slots if slot.barcode is not None}
    drive_barcodes = {str(drive.barcode) for drive in inventory.drives if drive.barcode is not None}
    assert slot_barcodes.isdisjoint(drive_barcodes)
