"""Sharded restore: reads from multiple drives in parallel and reassembles files."""

from __future__ import annotations

import concurrent.futures
import logging
import shutil
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError, ChecksumMismatchError
from openblade.domain.models import MountMode
from openblade.jobs.scheduler import DriveHandle, DriveScheduler
from openblade.jobs.shard import DEFAULT_BLOCK_SIZE, compute_checksum, reassemble_block_stripe
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

logger = logging.getLogger(__name__)


@dataclass
class ShardedRestoreRequest:
    catalog_path: str
    dest_path: Path
    block_size: int = DEFAULT_BLOCK_SIZE


@dataclass
class ShardedRestoreResult:
    job_id: str
    source_barcodes: list[str]
    checksum_verified: bool
    bytes_restored: int
    error: str | None = None


def _latest_archived_instance(record: Any) -> Any | None:
    archived = [instance for instance in record.instances if instance.state == "archived"]
    if not archived:
        return None
    return max(archived, key=lambda instance: instance.archived_at or instance.created_at)


def run_sharded_restore(
    request: ShardedRestoreRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    job_id: str,
) -> ShardedRestoreResult:
    """Restore a file from one or more tapes using parallel reads."""
    catalog.update_job_state(job_id, "running")
    file_record = catalog.get_file_record(request.catalog_path)
    if file_record is None:
        error = "File not found in catalog"
        catalog.update_job_state(job_id, "failed", error=error)
        return ShardedRestoreResult(
            job_id=job_id,
            source_barcodes=[],
            checksum_verified=False,
            bytes_restored=0,
            error=error,
        )

    shard_records = catalog.list_shard_records(file_record.id)
    if shard_records:
        ordered_shards = sorted(shard_records, key=lambda record: record.shard_index or 0)
        instances = [
            instance
            for instance in (_latest_archived_instance(record) for record in ordered_shards)
            if instance is not None
        ]
        if len(instances) != len(ordered_shards):
            error = "Missing archived shard instances"
            catalog.update_job_state(job_id, "failed", error=error)
            return ShardedRestoreResult(
                job_id=job_id,
                source_barcodes=[],
                checksum_verified=False,
                bytes_restored=0,
                error=error,
            )
        restore_block_size = file_record.block_size or request.block_size
    else:
        instances = sorted(
            [instance for instance in file_record.instances if instance.state == "archived"],
            key=lambda instance: instance.tape_path,
        )
        restore_block_size = request.block_size
    if not instances:
        error = "No archived instances found"
        catalog.update_job_state(job_id, "failed", error=error)
        return ShardedRestoreResult(
            job_id=job_id,
            source_barcodes=[],
            checksum_verified=False,
            bytes_restored=0,
            error=error,
        )

    request.dest_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir = _make_scratch_dir("restore")
    try:
        if len(instances) == 1:
            return _restore_single(
                instances[0],
                file_record,
                request,
                library,
                ltfs,
                catalog,
                scheduler,
                scratch_dir,
                job_id,
            )
        return _restore_sharded(
            instances,
            file_record,
            request,
            restore_block_size,
            library,
            ltfs,
            catalog,
            scheduler,
            scratch_dir,
            job_id,
        )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _restore_single(
    instance: Any,
    file_record: Any,
    request: ShardedRestoreRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    scratch_dir: Path,
    job_id: str,
) -> ShardedRestoreResult:
    cartridge = catalog.get_cartridge(instance.barcode)
    if cartridge is not None and cartridge.state == "exported":
        error = f"Cartridge {instance.barcode} is offline/exported"
        catalog.update_job_state(job_id, "failed", error=error)
        raise CartridgeOfflineError(error)

    handles = scheduler.acquire_drives([instance.barcode])
    handle = handles[0]
    drive_id = handle.drive_id
    slot_id: int | None = None

    try:
        drive_id, slot_id = _ensure_loaded(library, handle)
        mount = ltfs.mount(instance.barcode, MountMode.READ_ONLY)
        try:
            tmp_dest = scratch_dir / Path(instance.tape_path).name
            ltfs.read_file(mount, PurePosixPath(instance.tape_path), tmp_dest)
            actual_checksum = compute_checksum(tmp_dest)
            if actual_checksum != file_record.checksum_sha256:
                quarantine = scratch_dir / f"quarantine_{tmp_dest.name}"
                tmp_dest.rename(quarantine)
                error = (
                    f"Checksum mismatch: expected {file_record.checksum_sha256}, "
                    f"got {actual_checksum}"
                )
                catalog.update_job_state(job_id, "failed", error=error)
                raise ChecksumMismatchError(error)
            shutil.copy2(tmp_dest, request.dest_path)
        finally:
            ltfs.unmount(mount)
    finally:
        if slot_id is not None:
            with suppress(Exception):
                library.unload(drive_id, slot_id)
        scheduler.release_drives(handles)

    catalog.update_job_state(job_id, "completed")
    return ShardedRestoreResult(
        job_id=job_id,
        source_barcodes=[instance.barcode],
        checksum_verified=True,
        bytes_restored=file_record.size_bytes,
    )


def _restore_sharded(
    instances: list[Any],
    file_record: Any,
    request: ShardedRestoreRequest,
    block_size: int,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    scratch_dir: Path,
    job_id: str,
) -> ShardedRestoreResult:
    barcodes = [instance.barcode for instance in instances]
    for barcode in barcodes:
        cartridge = catalog.get_cartridge(barcode)
        if cartridge is not None and cartridge.state == "exported":
            error = f"Cartridge {barcode} is offline/exported"
            catalog.update_job_state(job_id, "failed", error=error)
            raise CartridgeOfflineError(error)

    handles = scheduler.acquire_drives(barcodes)
    shard_files: list[Path] = [Path()] * len(instances)
    mounts: dict[str, Any] = {}
    loaded_slots: dict[int, int | None] = {}

    try:
        for handle in handles:
            drive_id, slot_id = _ensure_loaded(library, handle)
            loaded_slots[drive_id] = slot_id
            mounts[handle.barcode] = ltfs.mount(handle.barcode, MountMode.READ_ONLY)

        def _read_shard(index: int, instance: Any) -> Path:
            shard_path = scratch_dir / f"shard_{index:04d}.tmp"
            ltfs.read_file(mounts[instance.barcode], PurePosixPath(instance.tape_path), shard_path)
            return shard_path

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(instances)) as pool:
            future_map = {
                pool.submit(_read_shard, index, instance): index
                for index, instance in enumerate(instances)
            }
            for future in concurrent.futures.as_completed(future_map):
                index = future_map[future]
                shard_files[index] = future.result()

        actual_checksum = reassemble_block_stripe(
            shard_files,
            request.dest_path,
            block_size,
        )
        if actual_checksum != file_record.checksum_sha256:
            if request.dest_path.exists():
                request.dest_path.unlink()
            error = (
                f"Reassembled checksum mismatch: {actual_checksum} != {file_record.checksum_sha256}"
            )
            catalog.update_job_state(job_id, "failed", error=error)
            raise ChecksumMismatchError(error)
    finally:
        for mount in mounts.values():
            with suppress(Exception):
                ltfs.unmount(mount)
        for handle in handles:
            slot_id = loaded_slots.get(handle.drive_id)
            if slot_id is not None:
                with suppress(Exception):
                    library.unload(handle.drive_id, slot_id)
        scheduler.release_drives(handles)

    catalog.update_job_state(job_id, "completed")
    return ShardedRestoreResult(
        job_id=job_id,
        source_barcodes=barcodes,
        checksum_verified=True,
        bytes_restored=file_record.size_bytes,
    )


def _ensure_loaded(
    library: MockLibraryBackend,
    handle: DriveHandle,
) -> tuple[int, int | None]:
    loaded_drive_id = library.find_drive_by_barcode(handle.barcode)
    if loaded_drive_id is not None:
        if loaded_drive_id != handle.drive_id:
            handle.drive_id = loaded_drive_id
        return loaded_drive_id, None

    slot_id = library.find_slot_by_barcode(handle.barcode)
    if slot_id is None:
        raise CartridgeOfflineError(f"Barcode {handle.barcode} not found in library")
    library.load(slot_id, handle.drive_id)
    return handle.drive_id, slot_id


def _make_scratch_dir(prefix: str) -> Path:
    scratch_dir = Path.cwd() / ".openblade-scratch" / f"{prefix}-{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=False)
    return scratch_dir
