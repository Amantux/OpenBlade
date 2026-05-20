"""Restore job: catalog lookup → tape → local path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from openblade.api import aml_state
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError, ChecksumMismatchError
from openblade.domain.models import JobType, MountMode
from openblade.jobs.queue import JobQueue
from openblade.jobs.verify import sha256sum
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend


@dataclass
class RestoreRequest:
    catalog_path: str
    dest_path: Path
    dry_run: bool = False


@dataclass
class RestoreResult:
    job_id: str
    source_barcode: str
    checksum_verified: bool
    error: str | None = None


def _aml_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _aml_drive_name(drive_id: int) -> str:
    return f"DRV-{drive_id + 1:03d}"


def _aml_slot_address(slot_id: int) -> str:
    return f"1,1,{slot_id}"


def _mark_aml_drive_busy(barcode: str, drive_id: int) -> None:
    drive_name = _aml_drive_name(drive_id)
    drive = aml_state.get_aml_drive(drive_name)
    if drive is not None:
        media = aml_state.get_aml_media(barcode)
        aml_state.update_aml_drive(
            drive_name,
            {
                "state": "busy",
                "loadedMedia": {
                    "barcode": barcode,
                    "type": (media or {}).get("type", "LTO-9"),
                    "state": "loaded",
                },
            },
        )
    media = aml_state.get_aml_media(barcode)
    if media is not None:
        aml_state.update_aml_media(
            barcode,
            {
                "slotAddress": drive_name,
                "state": "loaded",
                "lastLoaded": _aml_timestamp(),
                "loadCount": int(media.get("loadCount", 0)) + 1,
            },
        )


def _mark_aml_drive_idle(barcode: str, drive_id: int, slot_id: int | None) -> None:
    drive_name = _aml_drive_name(drive_id)
    if aml_state.get_aml_drive(drive_name) is not None:
        aml_state.update_aml_drive(drive_name, {"state": "idle", "loadedMedia": None})
    media = aml_state.get_aml_media(barcode)
    if media is not None and slot_id is not None:
        aml_state.update_aml_media(barcode, {"slotAddress": _aml_slot_address(slot_id), "state": "home"})


def _load_if_needed(library: MockLibraryBackend, barcode: str) -> tuple[int, int | None]:
    drive_id = library.find_drive_by_barcode(barcode)
    if drive_id is not None:
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise CartridgeOfflineError(f"Cartridge {barcode} is offline")
    drive_id = 0
    library.load(slot_id, drive_id)
    return drive_id, slot_id


def run_restore_job(
    request: RestoreRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    job_id: str,
) -> RestoreResult:
    """Restore a cataloged file from tape to a local path."""
    catalog.update_job_state(job_id, "running")
    record, instance = catalog.get_latest_instance_for_path(request.catalog_path)
    cartridge = catalog.get_cartridge(instance.barcode)
    if cartridge is not None and cartridge.state == "exported":
        catalog.update_job_state(job_id, "failed", f"Cartridge {instance.barcode} is offline")
        raise CartridgeOfflineError(f"Cartridge {instance.barcode} is offline")
    if request.dry_run:
        catalog.update_job_state(job_id, "completed")
        return RestoreResult(
            job_id=job_id, source_barcode=instance.barcode, checksum_verified=False
        )
    drive_id, slot_id = _load_if_needed(library, instance.barcode)
    _mark_aml_drive_busy(instance.barcode, drive_id)
    final_dest = (
        request.dest_path / PurePosixPath(request.catalog_path).name
        if request.dest_path.exists() and request.dest_path.is_dir()
        else request.dest_path
    )
    try:
        handle = ltfs.mount(instance.barcode, MountMode.READ_ONLY)
        try:
            ltfs.read_file(handle, PurePosixPath(instance.tape_path), final_dest)
        finally:
            ltfs.unmount(handle)
    finally:
        if slot_id is not None:
            library.unload(drive_id, slot_id)
        _mark_aml_drive_idle(instance.barcode, drive_id, slot_id)
    actual_checksum = sha256sum(final_dest)
    if actual_checksum != record.checksum_sha256:
        quarantine = final_dest.with_name(f"{final_dest.name}.quarantine")
        final_dest.rename(quarantine)
        catalog.update_job_state(job_id, "failed", f"Checksum mismatch for {request.catalog_path}")
        raise ChecksumMismatchError(f"Checksum mismatch for {request.catalog_path}")
    catalog.update_job_state(job_id, "completed")
    return RestoreResult(job_id=job_id, source_barcode=instance.barcode, checksum_verified=True)


class RestoreService:
    def __init__(
        self,
        library: MockLibraryBackend,
        ltfs: MockLTFSBackend,
        catalog: CatalogRepository,
        queue: JobQueue,
    ) -> None:
        self.library = library
        self.ltfs = ltfs
        self.catalog = catalog
        self.queue = queue

    def enqueue(self, catalog_path: str, destination: Path):
        job = self.catalog.create_job(
            JobType.RESTORE.value,
            {"catalog_path": catalog_path, "dest_path": str(destination)},
        )
        run_restore_job(
            RestoreRequest(catalog_path=catalog_path, dest_path=destination),
            self.library,
            self.ltfs,
            self.catalog,
            job.id,
        )
        refreshed = self.catalog.get_job(job.id)
        assert refreshed is not None
        return refreshed
