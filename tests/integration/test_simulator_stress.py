from __future__ import annotations

import hashlib
import random
from pathlib import Path, PurePosixPath

import pytest

from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.simulator.ltfs_volume import MockFileRecord
from openblade.simulator.scenarios import large_library


@pytest.mark.slow
def test_large_library_stress(tmp_path: Path) -> None:
    """100 cartridges, 4 drives, 10000 file records, random ops, seeded fault injection."""
    random.seed(12345)
    library, ltfs = large_library()

    for barcode in library.get_all_barcodes()[:10]:
        ltfs.format(
            barcode,
            FormatConfirmation(barcode, SafetyToken.generate("format", barcode)),
        )
        for file_index in range(1000):
            data = f"{barcode}-{file_index}".encode()
            checksum = hashlib.sha256(data).hexdigest()
            path = PurePosixPath(f"/seed/{barcode}/{file_index:04d}.bin")
            tape = ltfs.ensure_tape(barcode)
            tape.files[str(path)] = MockFileRecord(
                tape_path=str(path),
                size_bytes=len(data),
                checksum_sha256=checksum,
                content=data,
            )
            tape.used_bytes += len(data)

    for step in range(1000):
        inventory = library.inventory()
        action = random.choice(["load", "unload", "move", "write"])
        if action == "load":
            candidate_slots = [slot.slot_id for slot in inventory.slots if slot.occupied]
            candidate_drives = [
                drive.drive_id for drive in inventory.drives if drive.barcode is None
            ]
            if candidate_slots and candidate_drives:
                library.load(random.choice(candidate_slots), random.choice(candidate_drives))
        elif action == "unload":
            candidate_drives = [
                drive.drive_id for drive in inventory.drives if drive.barcode is not None
            ]
            candidate_slots = [slot.slot_id for slot in inventory.slots if not slot.occupied]
            if candidate_drives and candidate_slots:
                drive_id = random.choice(candidate_drives)
                if library.get_drive(drive_id).mount_state.value == "unmounted":
                    library.unload(drive_id, random.choice(candidate_slots))
        elif action == "move":
            candidate_sources = [slot.slot_id for slot in inventory.slots if slot.occupied]
            candidate_targets = [slot.slot_id for slot in inventory.slots if not slot.occupied]
            if candidate_sources and candidate_targets:
                source = random.choice(candidate_sources)
                target = random.choice(candidate_targets)
                if source != target:
                    library.move(source, target)
        else:
            loaded = [drive for drive in inventory.drives if drive.barcode is not None]
            if loaded:
                drive = random.choice(loaded)
                barcode = str(drive.barcode)
                tape = ltfs.ensure_tape(barcode)
                if not tape.formatted:
                    ltfs.format(
                        barcode,
                        FormatConfirmation(barcode, SafetyToken.generate("format", barcode)),
                    )
                handle = ltfs.mount(barcode, MountMode.READ_WRITE)
                payload = tmp_path / f"stress-{step}.bin"
                payload.write_bytes(f"record-{step}".encode())
                dest = PurePosixPath(f"/stress/{barcode}/{step:04d}.bin")
                try:
                    ltfs.write_file(handle, payload, dest)
                finally:
                    ltfs.unmount(handle)

        seen: set[str] = set()
        for slot in library.inventory().slots:
            if slot.barcode is not None:
                assert str(slot.barcode) not in seen
                seen.add(str(slot.barcode))
        for drive in library.inventory().drives:
            if drive.barcode is not None:
                assert str(drive.barcode) not in seen
                seen.add(str(drive.barcode))
        for barcode in library.get_all_barcodes():
            tape = ltfs.ensure_tape(barcode)
            assert tape.used_bytes >= 0
