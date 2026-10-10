"""Restore job: catalog lookup → tape → local path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from openblade.api import aml_state
from openblade.catalog.models import FileInstance, FileRecord, Job
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import (
    CartridgeOfflineError,
    ChecksumMismatchError,
    StaleLeaseError,
    safe_job_error,
)
from openblade.domain.errors import (
    FileNotFoundError as CatalogPathNotFoundError,
)
from openblade.domain.models import JobType, MountHandle, MountMode
from openblade.jobs.inventory import InventoryService
from openblade.jobs.queue import JobQueue
from openblade.jobs.reconcile import ensure_drive_reconciled
from openblade.jobs.scheduler import CatalogLeaseStore, DriveHandle, DriveScheduler, LeaseStore
from openblade.jobs.sharded_archive import record_physical_state_unknown
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
        ensure_drive_reconciled(catalog, drive_id, barcode)
        # Already in a drive: record it as the PHYSICAL drive; the lease key
        # (handle.drive_id) stays what the scheduler reserved.
        if drive_id != handle.drive_id:
            handle.physical_drive_id = drive_id
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise CartridgeOfflineError(f"Cartridge {barcode} is offline")
    drive_id = handle.drive_id
    ensure_drive_reconciled(catalog, drive_id, barcode)
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


@dataclass
class RestoreItemResult:
    catalog_path: str
    ok: bool
    error: str | None = None


@dataclass
class RestoreBatchResult:
    job_id: str
    items: list[RestoreItemResult]
    tape_errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.tape_errors and all(item.ok for item in self.items)


def _batch_dest(request: RestoreRequest) -> Path:
    if request.dest_path.exists() and request.dest_path.is_dir():
        return request.dest_path / PurePosixPath(request.catalog_path).name
    return request.dest_path


def _restore_one_verified(
    ltfs: LTFSBackend,
    mount_handle: MountHandle,
    request: RestoreRequest,
    record: FileRecord,
    instance: FileInstance,
) -> RestoreItemResult:
    """Read into a temp name; only a verified checksum renames it into place."""
    final_dest = _batch_dest(request)
    temp = final_dest.with_name(f".{final_dest.name}.restoring")
    try:
        ltfs.read_file(mount_handle, PurePosixPath(instance.tape_path), temp)
    except StaleLeaseError:
        raise
    except Exception as exc:  # per-item: one unreadable file must not abort the tape
        logger.exception("batch restore read failed for %s", request.catalog_path)
        temp.unlink(missing_ok=True)
        return RestoreItemResult(request.catalog_path, ok=False, error=safe_job_error(exc))
    if sha256sum(temp) != record.checksum_sha256:
        temp.rename(final_dest.with_name(f"{final_dest.name}.quarantine"))
        return RestoreItemResult(
            request.catalog_path, ok=False, error=f"Checksum mismatch for {request.catalog_path}"
        )
    temp.replace(final_dest)
    return RestoreItemResult(request.catalog_path, ok=True)


def _restore_tape_group(
    barcode: str,
    group: list[tuple[RestoreRequest, FileRecord, FileInstance]],
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository,
    job_id: str,
    scheduler: DriveScheduler,
) -> tuple[list[RestoreItemResult], str | None]:
    """One lease, one load, one RO mount, one unmount, one unload for ``group``."""
    results: list[RestoreItemResult] = []
    tape_error: str | None = None
    handles = scheduler.acquire_drives([barcode])
    handle = handles[0]
    catalog.journal(job_id, "lease_acquired", {"drive_id": handle.drive_id, "barcode": barcode})
    catalog.journal(job_id, "batch_tape_started", {"barcode": barcode, "files": len(group)})
    drive_id: int | None = None
    slot_id: int | None = None
    fenced_out = False
    unmount_failed = False
    try:
        try:
            drive_id, slot_id = _load_if_needed(catalog, library, ltfs, handle, job_id)
            scheduler.record_physical_drives(handles)
            _mark_aml_drive_busy(barcode, drive_id)
            scheduler.verify(handle)  # fencing: never mount on a stale lease
            mount_handle = ltfs.mount(barcode, MountMode.READ_ONLY)
            try:
                for request, record, instance in group:
                    results.append(
                        _restore_one_verified(ltfs, mount_handle, request, record, instance)
                    )
            finally:
                scheduler.verify(handle)  # fencing: before unmount
                try:
                    ltfs.unmount(mount_handle)
                except Exception:  # noqa: BLE001 - logged+journaled by record_physical_state_unknown
                    # Still (maybe) mounted: unloading now could strand a dirty
                    # index. Leave the cartridge in the drive for reconcile.
                    unmount_failed = True
                    # One payload shape ({op, barcode, drive}) for every producer. A
                    # journal failure flags the handle; release_drives keeps its lease.
                    record_physical_state_unknown(
                        catalog, job_id, "unmount", barcode, drive_id, handles=handles
                    )
                    tape_error = f"Unmount of {barcode} failed; left in drive for reconcile"
        except StaleLeaseError:
            fenced_out = True
            raise
        except Exception as exc:  # tape-level: load/mount failed -> fail this tape's items
            logger.exception("batch restore failed on tape %s", barcode)
            message = safe_job_error(exc)
            done = {item.catalog_path for item in results}
            results.extend(
                RestoreItemResult(req.catalog_path, ok=False, error=message)
                for req, _, _ in group
                if req.catalog_path not in done
            )
        finally:
            if not fenced_out and not unmount_failed and drive_id is not None:
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
                            barcode=barcode,
                            drive_id=drive_id,
                            slot_id=slot_id,
                            requested_by="restore-job",
                            job_id=job_id,
                        ),
                    )
                _mark_aml_drive_idle(barcode, drive_id, slot_id)
    finally:
        scheduler.release_drives(handles)
        catalog.journal(job_id, "lease_released", {"drive_id": handle.drive_id, "barcode": barcode})
        catalog.journal(
            job_id,
            "batch_tape_finished",
            {"barcode": barcode, "ok": sum(1 for r in results if r.ok), "files": len(group)},
        )
    return results, tape_error


def run_restore_batch(
    requests: list[RestoreRequest],
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository,
    job_id: str,
    *,
    scheduler: DriveScheduler | None = None,
) -> RestoreBatchResult:
    """Restore many cataloged files with ONE load/mount/unload per source tape.

    Requests are grouped by the barcode of each path's latest archived
    instance. A path with no archived instance (or on an exported cartridge)
    is a per-item failure, not a batch abort. A lost lease aborts the batch
    and leaves the hardware alone, exactly as ``run_restore_job`` does.
    """
    catalog.update_job_state(job_id, "running")
    items: list[RestoreItemResult] = []
    tape_errors: list[str] = []
    groups: dict[str, list[tuple[RestoreRequest, FileRecord, FileInstance]]] = {}
    for request in requests:
        try:
            record, instance = catalog.get_latest_instance_for_path(request.catalog_path)
        except CatalogPathNotFoundError:
            items.append(
                RestoreItemResult(
                    request.catalog_path,
                    ok=False,
                    error=f"No archived instance for {request.catalog_path}",
                )
            )
            continue
        cartridge = catalog.get_cartridge(instance.barcode)
        if cartridge is not None and cartridge.state == "exported":
            items.append(
                RestoreItemResult(
                    request.catalog_path,
                    ok=False,
                    error=f"Cartridge {instance.barcode} is offline",
                )
            )
            continue
        if request.dry_run:
            items.append(RestoreItemResult(request.catalog_path, ok=True))
            continue
        groups.setdefault(instance.barcode, []).append((request, record, instance))
    if groups and scheduler is None:
        scheduler = DriveScheduler(
            num_drives=len(InventoryService(library).snapshot().drives), job_id=job_id
        )
    for barcode, group in groups.items():
        assert scheduler is not None
        try:
            results, tape_error = _restore_tape_group(
                barcode, group, library, ltfs, catalog, job_id, scheduler
            )
        except StaleLeaseError as exc:
            logger.error(
                "batch job %s lost its lease on %s; leaving the drive untouched -- "
                "physical state unknown, reconcile before reuse",
                job_id,
                barcode,
            )
            catalog.update_job_state(job_id, "failed", safe_job_error(exc))
            raise
        items.extend(results)
        if tape_error is not None:
            tape_errors.append(tape_error)
    result = RestoreBatchResult(job_id=job_id, items=items, tape_errors=tape_errors)
    if result.ok:
        catalog.update_job_state(job_id, "completed")
    else:
        failed = sum(1 for item in items if not item.ok)
        detail = f"{failed} of {len(items)} files failed to restore"
        if tape_errors:
            detail = f"{detail}; {'; '.join(tape_errors)}"
        catalog.update_job_state(job_id, "failed", detail)
    return result


class RestoreService:
    def __init__(
        self,
        library: LibraryBackend,
        ltfs: LTFSBackend,
        catalog: CatalogRepository,
        queue: JobQueue,
        *,
        lease_store: LeaseStore | None = None,
    ) -> None:
        self.library = library
        self.ltfs = ltfs
        self.catalog = catalog
        self.queue = queue
        # Catalog-backed by default so this job's drives are excluded from every
        # other job, in this process or another one.
        self.lease_store = lease_store if lease_store is not None else CatalogLeaseStore(catalog)

    def _scheduler(self, job_id: str) -> DriveScheduler:
        return DriveScheduler(
            num_drives=len(InventoryService(self.library).snapshot().drives),
            store=self.lease_store,
            job_id=job_id,
        )

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
            scheduler=self._scheduler(job.id),
        )
        refreshed = self.catalog.get_job(job.id)
        assert refreshed is not None
        return refreshed

    def enqueue_batch(self, requests: list[RestoreRequest]) -> tuple[Job, RestoreBatchResult]:
        """One restore job covering every request; one load per source tape."""
        job = self.catalog.create_job(
            JobType.RESTORE.value,
            {
                "batch": [
                    {"catalog_path": r.catalog_path, "dest_path": str(r.dest_path)}
                    for r in requests
                ]
            },
        )
        result = run_restore_batch(
            requests,
            self.library,
            self.ltfs,
            self.catalog,
            job.id,
            scheduler=self._scheduler(job.id),
        )
        refreshed = self.catalog.get_job(job.id)
        assert refreshed is not None
        return refreshed, result
