from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import pytest

from openblade.domain.errors import (
    BarcodeMismatchError,
    DriveOccupiedError,
    FormatRequiresConfirmationError,
    SlotEmptyError,
    SlotOccupiedError,
    TapeFullError,
    TapeMountedError,
)
from openblade.domain.models import MountMode, MountState
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.simulator.faults import FaultConfig
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import (
    empty_library,
    one_drive_twenty_slots_five_cartridges,
    two_drive_library,
)


def _confirmation(barcode: str) -> FormatConfirmation:
    return FormatConfirmation(barcode, SafetyToken.generate("format", barcode))


def _prepare_loaded_formatted_tape() -> tuple[MockLibraryBackend, MockLTFSBackend, str]:
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.get_slot(1).barcode)
    library.load(1, 0)
    ltfs.format(barcode, _confirmation(barcode))
    return library, ltfs, barcode


def test_empty_library_inventory() -> None:
    library, ltfs = empty_library()

    inventory = library.inventory()

    assert inventory.library_id == "mock-i3-001"
    assert len(inventory.slots) == 20
    assert len(inventory.drives) == 1
    assert all(not slot.occupied for slot in inventory.slots)
    assert all(drive.barcode is None for drive in inventory.drives)
    assert ltfs.to_json()["tapes"] == {}


def test_load_and_unload_updates_inventory() -> None:
    library, _ = one_drive_twenty_slots_five_cartridges()

    load_result = library.load(1, 0)
    assert load_result.success is True
    assert library.get_slot(1).occupied is False
    assert str(library.get_drive(0).barcode) == "PHO001L8"

    unload_result = library.unload(0, 10)
    assert unload_result.success is True
    assert library.get_drive(0).barcode is None
    assert str(library.get_slot(10).barcode) == "PHO001L8"


def test_illegal_library_operations_raise_typed_errors() -> None:
    library, _ = one_drive_twenty_slots_five_cartridges()

    with pytest.raises(SlotEmptyError):
        library.load(10, 0)

    library.load(1, 0)
    with pytest.raises(DriveOccupiedError):
        library.load(2, 0)

    with pytest.raises(SlotOccupiedError):
        library.unload(0, 2)


def test_unload_rejects_mounted_tape() -> None:
    library, ltfs, barcode = _prepare_loaded_formatted_tape()

    handle = ltfs.mount(barcode, MountMode.READ_ONLY)

    with pytest.raises(TapeMountedError):
        library.unload(0, 1)

    ltfs.unmount(handle)
    library.unload(0, 1)


def test_format_requires_confirmation_and_matching_barcode() -> None:
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.get_slot(1).barcode)

    with pytest.raises(FormatRequiresConfirmationError):
        ltfs.format(barcode, None)

    with pytest.raises(BarcodeMismatchError):
        ltfs.format(barcode, _confirmation("PHO002L8"))

    result = ltfs.format(barcode, _confirmation(barcode))
    assert result.success is True


def test_mount_write_read_stat_and_capacity_tracking(tmp_path: Path) -> None:
    library, ltfs, barcode = _prepare_loaded_formatted_tape()

    ro_handle = ltfs.mount(barcode, MountMode.READ_ONLY)
    assert ro_handle.mode is MountMode.READ_ONLY
    ltfs.unmount(ro_handle)

    rw_handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    assert rw_handle.mode is MountMode.READ_WRITE

    source = tmp_path / "payload.bin"
    source.write_bytes(b"payload-data")
    file_instance = ltfs.write_file(rw_handle, source, PurePosixPath("/archive/payload.bin"))
    assert file_instance.state.value == "archived"

    stat = ltfs.stat(rw_handle, PurePosixPath("/archive/payload.bin"))
    expected_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    assert stat.checksum_sha256 == expected_checksum
    assert stat.size_bytes == len(source.read_bytes())

    restored = tmp_path / "restored.bin"
    read_result = ltfs.read_file(rw_handle, PurePosixPath("/archive/payload.bin"), restored)
    assert restored.read_bytes() == source.read_bytes()
    assert read_result.details["checksum"] == expected_checksum
    assert ltfs.ensure_tape(barcode).used_bytes == len(source.read_bytes())
    assert ltfs.remaining_capacity(barcode) == ltfs.ensure_tape(barcode).capacity_bytes - len(
        source.read_bytes()
    )


def test_tape_full_raises() -> None:
    library = MockLibraryBackend(num_slots=2, num_drives=1)
    library.add_cartridge(1, "FULL0001")
    ltfs = MockLTFSBackend(library, capacity_bytes=4)
    library.load(1, 0)
    ltfs.format("FULL0001", _confirmation("FULL0001"))
    handle = ltfs.mount("FULL0001", MountMode.READ_WRITE)
    payload = Path("tests-data-full.bin")
    try:
        payload.write_bytes(b"12345")
        with pytest.raises(TapeFullError):
            ltfs.write_file(handle, payload, PurePosixPath("/too-large.bin"))
    finally:
        if payload.exists():
            payload.unlink()


def test_dirty_unmount_state_blocks_unload(tmp_path: Path) -> None:
    library, _, barcode = _prepare_loaded_formatted_tape()
    ltfs = MockLTFSBackend(
        library,
        fault_config=FaultConfig(write_fail_after_bytes=4),
    )
    ltfs.format(barcode, _confirmation(barcode))
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "partial.bin"
    payload.write_bytes(b"partial-write")

    with pytest.raises(Exception):
        ltfs.write_file(handle, payload, PurePosixPath("/partial.bin"))

    unmount_result = ltfs.unmount(handle)
    assert unmount_result.success is True
    assert library.get_drive(0).mount_state is MountState.DIRTY
    with pytest.raises(TapeMountedError):
        library.unload(0, 1)


def test_persistence_round_trip_rebuilds_inventory(tmp_path: Path) -> None:
    library, ltfs, barcode = _prepare_loaded_formatted_tape()
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "persist.bin"
    payload.write_bytes(b"persist-me")
    ltfs.write_file(handle, payload, PurePosixPath("/persist.bin"))
    ltfs.unmount(handle)

    restored_library = MockLibraryBackend.from_json(library.to_json())
    restored_ltfs = MockLTFSBackend.from_json(restored_library, ltfs.to_json())

    assert restored_library.inventory() == library.inventory()
    restored_tape = restored_ltfs.ensure_tape(barcode)
    assert restored_tape.used_bytes == ltfs.ensure_tape(barcode).used_bytes
    assert (
        restored_tape.files["/persist.bin"].checksum_sha256
        == hashlib.sha256(payload.read_bytes()).hexdigest()
    )


def test_two_drive_library_can_load_two_cartridges() -> None:
    library, _ = two_drive_library()

    library.load(1, 0)
    library.load(2, 1)

    assert str(library.get_drive(0).barcode) == "TWO001L8"
    assert str(library.get_drive(1).barcode) == "TWO002L8"
