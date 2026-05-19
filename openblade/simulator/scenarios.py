"""Pre-built simulator scenarios for testing."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from openblade.domain.models import CartridgeState, DriveState, MountState
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockFileRecord, MockLTFSBackend


def _barcode(prefix: str, number: int) -> str:
    return f"{prefix}{number:06d}"[:8].upper()


def _paired(
    barcodes: list[str],
    *,
    slots: int = 20,
    drives: int = 1,
    capacity_bytes: int = 4096,
) -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library = MockLibraryBackend(num_slots=slots, num_drives=drives)
    for slot_id, barcode in zip(range(1, slots + 1), barcodes, strict=False):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=capacity_bytes)
    return library, ltfs


def _format_tape(ltfs: MockLTFSBackend, barcode: str) -> None:
    ltfs.format(
        barcode,
        FormatConfirmation(
            expected_barcode=barcode,
            safety_token=SafetyToken.generate("format", barcode),
        ),
    )


def _add_seed_file(ltfs: MockLTFSBackend, barcode: str, path: str, data: bytes) -> None:
    tape = ltfs.ensure_tape(barcode)
    checksum = hashlib.sha256(data).hexdigest()
    tape.files[path] = MockFileRecord(
        tape_path=path,
        size_bytes=len(data),
        checksum_sha256=checksum,
        content=data,
    )
    tape.used_bytes += len(data)


def empty_library(
    num_slots: int = 20,
    num_drives: int = 1,
) -> tuple[MockLibraryBackend, MockLTFSBackend]:
    return _paired([], slots=num_slots, drives=num_drives)


def one_drive_twenty_slots_five_cartridges() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    return _paired([f"PHO00{i}L8" for i in range(1, 6)])


def partially_full_library() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired([f"ARC00{i}L8" for i in range(1, 6)])
    _format_tape(ltfs, "ARC001L8")
    _add_seed_file(ltfs, "ARC001L8", str(PurePosixPath("/archive/existing.bin")), b"x" * 1024)
    _format_tape(ltfs, "ARC002L8")
    return library, ltfs


def no_scratch_media() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired([f"USE00{i}L8" for i in range(1, 4)], capacity_bytes=512)
    for barcode in library.get_all_barcodes():
        _format_tape(ltfs, barcode)
        tape = ltfs.ensure_tape(barcode)
        tape.used_bytes = tape.capacity_bytes
    return library, ltfs


def dirty_tape_scenario() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired(["DIRTY1L8"])
    library.load(1, 0)
    _format_tape(ltfs, "DIRTY1L8")
    tape = ltfs.ensure_tape("DIRTY1L8")
    tape.mount_state = MountState.DIRTY
    library._drives[0].mount_state = MountState.DIRTY
    library._drives[0].drive_state = DriveState.BUSY
    return library, ltfs


def two_drive_library() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    return _paired([f"TWO00{i}L8" for i in range(1, 7)], drives=2)


def full_tape_scenario() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired(["FULL0001"], capacity_bytes=4)
    _format_tape(ltfs, "FULL0001")
    tape = ltfs.ensure_tape("FULL0001")
    tape.used_bytes = tape.capacity_bytes
    return library, ltfs


def failed_drive_scenario() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired(["FAIL0001"], drives=2)
    library.load(1, 0)
    library._drives[0].drive_state = DriveState.FAILED
    return library, ltfs


def missing_cartridge_scenario() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired(["MISS0001", "MISS0002"])
    missing_barcode = library.remove_cartridge(1)
    library._cartridge_states[missing_barcode] = CartridgeState.MISSING
    return library, ltfs


def cleaning_cartridge_scenario() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library, ltfs = _paired(["CLN00001"])
    library._cartridge_states["CLN00001"] = CartridgeState.CLEANING
    return library, ltfs


def large_library(
    num_slots: int = 100,
    num_drives: int = 4,
    num_cartridges: int = 80,
) -> tuple[MockLibraryBackend, MockLTFSBackend]:
    barcodes = [_barcode("LG", index) for index in range(1, num_cartridges + 1)]
    return _paired(barcodes, slots=num_slots, drives=num_drives, capacity_bytes=65_536)
