"""Restore job: catalog lookup → tape → local path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from openblade.api import aml_state
from openblade.catalog.models import Job
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import (
    CartridgeOfflineError,
    ChecksumMismatchError,
    StaleLeaseError,
    safe_job_error,
)
from openblade.domain.models import JobType, MountMode
from openblade.jobs.inventory import InventoryService
from openblade.jobs.queue import JobQueue
from openblade.jobs.scheduler import DriveHandle, DriveScheduler
from openblade.jobs.verify import sha256sum
from openblade.nas.tape_orchestrator import execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType

logger = logging.getLogger(__name__)


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
        aml_state.update_aml_media(
            barcode, {"slotAddress": _aml_slot_address(slot_id), "state": "home"}
        )


def _load_if_needed(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    handle: DriveHandle,
    job_id: str,
) -> tuple[int, int | None]:
    barcode = handle.barcode
    drive_id = library.find_drive_by_barcode(barcode)
    if drive_id is not None:
        # Already in a drive: record it as the PHYSICAL drive; the lease key
        # (handle.drive_id) stays what the scheduler reserved.
        if drive_id != handle.drive_id:
            handle.physical_drive_id = drive_id
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise CartridgeOfflineError(f"Cartridge {barcode} is offline")
    drive_id = handle.drive_id
    execute_tape_request(
        catalog,
        library,
        ltfs,
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode=barcode,
            drive_id=drive_id,
            slot_id=slot_id,
            requested_by="restore-job",
            job_id=job_id,
        ),
        raise_on_failed=True,
    )
    return drive_id, slot_id


def run_restore_job(
    request: RestoreRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository,
    job_id: str,
    *,
    scheduler: DriveScheduler | None = None,
) -> RestoreResult:
    """Restore a cataloged file from tape to a local path.

    The drive is taken through ``scheduler`` (a lease shared with every other job
    on the same store). ``None`` falls back to a job-local in-memory scheduler,
    which serialises nothing across jobs -- callers that can reach the app's
    lease store must pass one.
    """
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
    if scheduler is None:
        scheduler = DriveScheduler(
            num_drives=len(InventoryService(library).snapshot().drives), job_id=job_id
        )
    handles = scheduler.acquire_drives([instance.barcode])
    handle = handles[0]
    catalog.journal(
        job_id, "lease_acquired", {"drive_id": handle.drive_id, "barcode": handle.barcode}
    )
    final_dest = (
        request.dest_path / PurePosixPath(request.catalog_path).name
        if request.dest_path.exists() and request.dest_path.is_dir()
        else request.dest_path
    )
    drive_id: int | None = None
    slot_id: int | None = None
    fenced_out = False
    try:
        try:
            drive_id, slot_id = _load_if_needed(catalog, library, ltfs, handle, job_id)
            scheduler.record_physical_drives(handles)
            _mark_aml_drive_busy(instance.barcode, drive_id)
            scheduler.verify(handle)  # fencing: never mount on a stale lease
            mount_handle = ltfs.mount(instance.barcode, MountMode.READ_ONLY)
            try:
                ltfs.read_file(mount_handle, PurePosixPath(instance.tape_path), final_dest)
            finally:
                scheduler.verify(handle)  # fencing: before unmount
                ltfs.unmount(mount_handle)
        except StaleLeaseError:
            fenced_out = True
            raise
        finally:
            if not fenced_out and drive_id is not None:
                try:
                    scheduler.verify(handle)  # fencing: before unload
                except StaleLeaseError:
                    fenced_out = True
                    raise
                if slot_id is not None:
                    execute_tape_request(
                        catalog,
                        library,
                        ltfs,
                        TapeOpRequest(
                            op_type=TapeOpType.UNLOAD,
                            barcode=instance.barcode,
                            drive_id=drive_id,
                            slot_id=slot_id,
                            requested_by="restore-job",
                            job_id=job_id,
                        ),
                    )
                _mark_aml_drive_idle(instance.barcode, drive_id, slot_id)
    except StaleLeaseError as exc:
        # Fenced out: leave the hardware ALONE -- whoever holds the lease now may
        # have their own tape in that drive. Physical state is unknown -> reconcile.
        logger.error(
            "job %s lost its lease on drive %d (%s); leaving the drive untouched -- "
            "physical state unknown, reconcile before reuse",
            job_id,
            handle.physical,
            handle.barcode,
        )
        catalog.update_job_state(job_id, "failed", safe_job_error(exc))
        raise
    finally:
        scheduler.release_drives(handles)
        catalog.journal(
            job_id, "lease_released", {"drive_id": handle.drive_id, "barcode": handle.barcode}
        )
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
        library: LibraryBackend,
        ltfs: LTFSBackend,
        catalog: CatalogRepository,
        queue: JobQueue,
    ) -> None:
        self.library = library
        self.ltfs = ltfs
        self.catalog = catalog
        self.queue = queue

    def enqueue(self, catalog_path: str, destination: Path) -> Job:
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
