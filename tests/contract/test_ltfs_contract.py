"""LTFSBackend behavioural contract (mount/write/read/checksum + unload safety gate)."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

import pytest

from openblade.domain.errors import TapeMountedError
from openblade.domain.models import MountMode
from openblade.jobs.inventory import InventoryService
from tests.contract.conftest import BackendPair

pytestmark = pytest.mark.contract


def _load_first(pair: BackendPair) -> tuple[str, int, int]:
    barcode = pair.barcodes[0]
    slot = pair.library.find_slot_by_barcode(barcode)
    assert slot is not None
    drive_id = InventoryService(pair.library).snapshot().drives[0].drive_id
    assert pair.library.load(slot, drive_id).success
    return barcode, slot, drive_id


def test_mount_rw_write_then_ro_read_round_trips_checksum(
    backend_pair: BackendPair, tmp_path: Path
) -> None:
    backend_pair.format_all()
    barcode, slot, drive_id = _load_first(backend_pair)
    payload = bytes(range(256)) * 64
    source = tmp_path / "in.bin"
    source.write_bytes(payload)
    dest = PurePosixPath("/contract/in.bin")

    handle = backend_pair.ltfs.mount(barcode, MountMode.READ_WRITE)
    backend_pair.ltfs.write_file(handle, source, dest)
    stat = backend_pair.ltfs.stat(handle, dest)
    assert stat.size_bytes == len(payload)
    assert stat.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert backend_pair.ltfs.unmount(handle).success

    handle = backend_pair.ltfs.mount(barcode, MountMode.READ_ONLY)
    restored = tmp_path / "out.bin"
    assert backend_pair.ltfs.read_file(handle, dest, restored).success
    assert backend_pair.ltfs.unmount(handle).success

    assert hashlib.sha256(restored.read_bytes()).digest() == hashlib.sha256(payload).digest()
    assert backend_pair.library.unload(drive_id, slot).success


def test_unload_while_mounted_is_refused(
    backend_pair: BackendPair, request: pytest.FixtureRequest
) -> None:
    if backend_pair.name == "emulator+sim-ltfs":
        request.applymarker(
            pytest.mark.xfail(
                strict=True,
                reason=(
                    "openblade/hardware/scalar_http/library_backend.py:260 unload() has no "
                    "LTFS mount-state gate; the AML emulator moves a mounted cartridge"
                ),
            )
        )
    backend_pair.format_all()
    barcode, slot, drive_id = _load_first(backend_pair)
    handle = backend_pair.ltfs.mount(barcode, MountMode.READ_WRITE)

    with pytest.raises(TapeMountedError):
        backend_pair.library.unload(drive_id, slot)

    assert backend_pair.library.find_drive_by_barcode(barcode) == drive_id
    assert backend_pair.ltfs.unmount(handle).success
    assert backend_pair.library.unload(drive_id, slot).success
