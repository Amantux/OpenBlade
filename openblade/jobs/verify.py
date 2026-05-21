"""Verification helpers for LTFS content."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from openblade.catalog.models import FileRecord
from openblade.catalog.repository import CatalogRepository
from openblade.domain.models import MountMode
from openblade.nas.tape_orchestrator import execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_if_needed(
    catalog: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    barcode: str,
) -> tuple[int, int | None]:
    drive_id = library.find_drive_by_barcode(barcode)
    if drive_id is not None:
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise FileNotFoundError(barcode)
    drive_id = 0
    execute_tape_request(
        catalog,
        library,
        ltfs,
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode=barcode,
            drive_id=drive_id,
            slot_id=slot_id,
            requested_by="verify-job",
        ),
        raise_on_failed=True,
    )
    return drive_id, slot_id


def run_verify_job(
    barcode: str,
    catalog: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
) -> dict[str, object]:
    """Mount a tape read-only and verify its archived catalog entries."""
    _, slot_id = _load_if_needed(catalog, library, ltfs, barcode)
    checked = 0
    try:
        handle = ltfs.mount(barcode, MountMode.READ_ONLY)
        try:
            for instance in catalog.list_instances_for_barcode(barcode):
                record = catalog.session.get(FileRecord, instance.file_record_id)
                if record is None:
                    continue
                stat = ltfs.stat(handle, PurePosixPath(instance.tape_path))
                if stat.checksum_sha256 != record.checksum_sha256:
                    raise ValueError(f"Checksum mismatch for {record.path}")
                checked += 1
        finally:
            ltfs.unmount(handle)
    finally:
        if slot_id is not None:
            execute_tape_request(
                catalog,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.UNLOAD,
                    barcode=barcode,
                    drive_id=0,
                    slot_id=slot_id,
                    requested_by="verify-job",
                ),
            )
    return {"barcode": barcode, "files_verified": checked}
