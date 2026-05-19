from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import pytest

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig
from openblade.domain.errors import (
    ChecksumMismatchError,
    DriveOccupiedError,
    SimulatedMountFailure,
    SimulatedRobotTimeout,
    SimulatedWriteFailure,
    SlotOccupiedError,
    TapeFullError,
    TapeMountedError,
)
from openblade.domain.models import MountMode
from openblade.simulator.faults import FaultConfig, FaultType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


def _confirmation(barcode: str):
    from openblade.domain.policies import FormatConfirmation, SafetyToken

    return FormatConfirmation(barcode, SafetyToken.generate("format", barcode))


def _loaded_formatted_backend(
    *,
    library_faults: FaultConfig | None = None,
    ltfs_faults: FaultConfig | None = None,
) -> tuple[MockLibraryBackend, MockLTFSBackend, str]:
    library = MockLibraryBackend(num_slots=4, num_drives=1, fault_config=library_faults)
    library.add_cartridge(1, "PHO001L8")
    barcode = "PHO001L8"
    library.load(1, 0)
    ltfs = MockLTFSBackend(library, capacity_bytes=64, fault_config=ltfs_faults)
    ltfs.format(barcode, _confirmation(barcode))
    return library, ltfs, barcode


def _archive_context(tmp_path: Path, fault_config: FaultConfig) -> tuple[object, str, Path]:
    config = OpenBladeConfig(db_url=f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    context = create_context(config)
    barcode = str(context.library.inventory().slots[0].barcode)
    context.library.load(1, 0)
    context.ltfs = MockLTFSBackend(context.library, capacity_bytes=64, fault_config=fault_config)
    context.ltfs.format(barcode, _confirmation(barcode))
    context.library.unload(0, 1)
    context.archive_service.ltfs = context.ltfs
    context.restore_service.ltfs = context.ltfs
    group = context.catalog.create_volume_group("faults")
    context.catalog.add_barcode_to_volume_group(group.id, barcode)
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.bin").write_bytes(b"payload")
    return context, barcode, source


def test_load_failure_raises_timeout() -> None:
    library = MockLibraryBackend(fault_config=FaultConfig.with_fault(FaultType.LOAD_FAILURE))
    library.add_cartridge(1, "PHO001L8")

    with pytest.raises(SimulatedRobotTimeout):
        library.load(1, 0)


def test_unload_failure_raises_timeout() -> None:
    library = MockLibraryBackend(
        num_slots=4, num_drives=1, fault_config=FaultConfig.with_fault(FaultType.UNLOAD_FAILURE)
    )
    library.add_cartridge(1, "PHO001L8")
    library.load(1, 0)

    with pytest.raises(SimulatedRobotTimeout):
        library.unload(0, 2)


def test_mount_failure_raises() -> None:
    _, ltfs, barcode = _loaded_formatted_backend(
        ltfs_faults=FaultConfig.with_fault(FaultType.MOUNT_FAILURE)
    )

    with pytest.raises(SimulatedMountFailure):
        ltfs.mount(barcode, MountMode.READ_ONLY)


def test_write_failure_raises(tmp_path: Path) -> None:
    _, ltfs, barcode = _loaded_formatted_backend(
        ltfs_faults=FaultConfig.with_fault(FaultType.WRITE_FAILURE)
    )
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")

    with pytest.raises(SimulatedWriteFailure):
        ltfs.write_file(handle, payload, PurePosixPath("/payload.bin"))


def test_partial_write_leaves_no_complete_record(tmp_path: Path) -> None:
    _, ltfs, barcode = _loaded_formatted_backend(ltfs_faults=FaultConfig(write_fail_after_bytes=2))
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")

    with pytest.raises(SimulatedWriteFailure):
        ltfs.write_file(handle, payload, PurePosixPath("/payload.bin"))

    assert "/payload.bin" not in ltfs.ensure_tape(barcode).files


def test_checksum_mismatch_read_returns_bad_checksum(tmp_path: Path) -> None:
    _, ltfs, barcode = _loaded_formatted_backend(
        ltfs_faults=FaultConfig.with_fault(FaultType.CHECKSUM_MISMATCH)
    )
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    ltfs.write_file(handle, payload, PurePosixPath("/payload.bin"))
    restored = tmp_path / "restored.bin"

    result = ltfs.read_file(handle, PurePosixPath("/payload.bin"), restored)

    assert restored.read_bytes() == payload.read_bytes()
    assert result.details["checksum"] != hashlib.sha256(restored.read_bytes()).hexdigest()


def test_no_free_space_raises_immediately(tmp_path: Path) -> None:
    _, ltfs, barcode = _loaded_formatted_backend(
        ltfs_faults=FaultConfig.with_fault(FaultType.NO_FREE_SPACE)
    )
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")

    with pytest.raises(TapeFullError):
        ltfs.write_file(handle, payload, PurePosixPath("/payload.bin"))


def test_changer_timeout_raises() -> None:
    library = MockLibraryBackend(fault_config=FaultConfig.with_fault(FaultType.CHANGER_TIMEOUT))
    library.add_cartridge(1, "PHO001L8")

    with pytest.raises(SimulatedRobotTimeout):
        library.load(1, 0)


def test_drive_already_loaded_raises_without_faults() -> None:
    library = MockLibraryBackend(num_slots=3, num_drives=1)
    library.add_cartridge(1, "PHO001L8")
    library.add_cartridge(2, "PHO002L8")
    library.load(1, 0)

    with pytest.raises(DriveOccupiedError):
        library.load(2, 0)


def test_target_slot_occupied_raises_without_faults() -> None:
    library = MockLibraryBackend(num_slots=3, num_drives=1)
    library.add_cartridge(1, "PHO001L8")
    library.add_cartridge(2, "PHO002L8")
    library.load(1, 0)

    with pytest.raises(SlotOccupiedError):
        library.unload(0, 2)


def test_tape_mounted_during_unload_attempt_raises() -> None:
    library, ltfs, barcode = _loaded_formatted_backend()
    handle = ltfs.mount(barcode, MountMode.READ_ONLY)

    with pytest.raises(TapeMountedError):
        library.unload(0, 2)

    ltfs.unmount(handle)


def test_partial_write_does_not_mark_file_archived(tmp_path: Path) -> None:
    context, barcode, source = _archive_context(tmp_path, FaultConfig(write_fail_after_bytes=2))

    with pytest.raises(SimulatedWriteFailure):
        context.archive_service.enqueue("faults", source)

    assert context.catalog.list_files("/faults") == []
    assert context.ltfs.ensure_tape(barcode).files == {}


def test_checksum_mismatch_after_archive_does_not_mark_archived(tmp_path: Path) -> None:
    context, _, source = _archive_context(
        tmp_path,
        FaultConfig.with_fault(FaultType.CHECKSUM_MISMATCH),
    )

    with pytest.raises(ChecksumMismatchError):
        context.archive_service.enqueue("faults", source)

    assert context.catalog.list_files("/faults") == []
