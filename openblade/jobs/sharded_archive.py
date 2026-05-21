"""Sharded archive job: writes to multiple drives in parallel."""

from __future__ import annotations

import concurrent.futures
import logging
import shutil
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from openblade.catalog.repository import CatalogRepository
from openblade.domain.models import MountMode
from openblade.jobs.scheduler import DriveHandle, DriveScheduler
from openblade.jobs.shard import (
    DEFAULT_BLOCK_SIZE,
    ShardMode,
    ShardSpec,
    compute_checksum,
    plan_block_stripe,
    write_shard_to_tempfile,
)
from openblade.nas.tape_orchestrator import execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

logger = logging.getLogger(__name__)


@dataclass
class ShardedArchiveRequest:
    source_path: Path
    volume_group_name: str
    lane_barcodes: list[str]
    mode: ShardMode = ShardMode.STRIPE
    block_size: int = DEFAULT_BLOCK_SIZE
    dry_run: bool = False


@dataclass
class ShardedArchiveResult:
    job_id: str
    files_archived: int
    bytes_archived: int
    tapes_used: list[str]
    shard_group_ids: list[str]
    errors: list[str]


def _shard_record_path(source_file: Path, shard_index: int) -> str:
    return f"{source_file}#shard{shard_index:04d}"


def _archive_profile(mode: ShardMode) -> str:
    return mode.value


def run_sharded_archive(
    request: ShardedArchiveRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    job_id: str,
) -> ShardedArchiveResult:
    """Archive files to tape using multiple drives in parallel."""
    source = request.source_path
    files = sorted(source.rglob("*") if source.is_dir() else [source])
    files = [path for path in files if path.is_file()]
    catalog.update_job_state(job_id, "running")

    if request.dry_run:
        result = ShardedArchiveResult(
            job_id=job_id,
            files_archived=len(files),
            bytes_archived=sum(path.stat().st_size for path in files),
            tapes_used=list(request.lane_barcodes),
            shard_group_ids=[],
            errors=[],
        )
        catalog.update_job_state(job_id, "completed")
        return result

    if not files:
        result = ShardedArchiveResult(
            job_id=job_id,
            files_archived=0,
            bytes_archived=0,
            tapes_used=list(request.lane_barcodes),
            shard_group_ids=[],
            errors=[],
        )
        catalog.update_job_state(job_id, "completed")
        return result

    volume_group = catalog.get_volume_group(request.volume_group_name)
    if volume_group is None:
        volume_group = catalog.create_volume_group(request.volume_group_name)
    for barcode in request.lane_barcodes:
        if catalog.get_cartridge(barcode) is None:
            catalog.add_cartridge(barcode, volume_group.id)

    bytes_archived = 0
    files_archived = 0
    shard_group_ids: list[str] = []
    errors: list[str] = []
    scratch_dir = _make_scratch_dir("archive")

    try:
        if request.mode == ShardMode.BLOCK_STRIPE and len(request.lane_barcodes) >= 2:
            for source_file in files:
                try:
                    _archive_block_stripe(
                        source_file,
                        request,
                        library,
                        ltfs,
                        catalog,
                        scheduler,
                        scratch_dir,
                        volume_group.id,
                        shard_group_ids,
                        job_id,
                    )
                    bytes_archived += source_file.stat().st_size
                    files_archived += 1
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Shard archive failed for %s", source_file)
                    errors.append(str(exc))
        else:
            files_archived, bytes_archived = _archive_stripe(
                files,
                request,
                library,
                ltfs,
                catalog,
                scheduler,
                volume_group.id,
                shard_group_ids,
                errors,
                job_id,
            )
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    state = "completed" if not errors else "failed_recoverable"
    catalog.update_job_state(job_id, state)
    return ShardedArchiveResult(
        job_id=job_id,
        files_archived=files_archived,
        bytes_archived=bytes_archived,
        tapes_used=list(request.lane_barcodes),
        shard_group_ids=shard_group_ids,
        errors=errors,
    )


def _archive_stripe(
    files: list[Path],
    request: ShardedArchiveRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    vg_id: str,
    shard_group_ids: list[str],
    errors: list[str],
    job_id: str,
) -> tuple[int, int]:
    lane_count = len(request.lane_barcodes)
    batches: list[list[tuple[Path, str]]] = []
    current_batch: list[tuple[Path, str]] = []

    for index, source_file in enumerate(files):
        barcode = request.lane_barcodes[index % lane_count]
        current_batch.append((source_file, barcode))
        if len(current_batch) == lane_count:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)

    files_archived = 0
    bytes_archived = 0
    for batch in batches:
        batch_barcodes = list(dict.fromkeys(barcode for _, barcode in batch))
        handles = scheduler.acquire_drives(batch_barcodes)
        mounts: dict[str, object] = {}
        loaded_slots: dict[int, int | None] = {}
        try:
            for handle in handles:
                drive_id, slot_id = _load_barcode(catalog, library, ltfs, handle, job_id)
                loaded_slots[drive_id] = slot_id
                mounts[handle.barcode] = ltfs.mount(handle.barcode, MountMode.READ_WRITE)

            def _write_one(
                source_file: Path,
                barcode: str,
                mount: object,
            ) -> tuple[Path, str, str, str, int]:
                tape_path = f"/stripe/{source_file.name}"
                checksum = compute_checksum(source_file)
                ltfs.write_file(mount, source_file, PurePosixPath(tape_path))
                stat = ltfs.stat(mount, PurePosixPath(tape_path))
                if stat.checksum_sha256 != checksum:
                    raise ValueError(f"Checksum mismatch: {source_file.name}")
                return source_file, barcode, tape_path, checksum, source_file.stat().st_size

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = [
                    pool.submit(_write_one, source_file, barcode, mounts[barcode])
                    for source_file, barcode in batch
                ]
                for future in concurrent.futures.as_completed(futures):
                    source_file, barcode, tape_path, checksum, size_bytes = future.result()
                    file_record = catalog.create_file_record(
                        path=str(source_file),
                        size_bytes=size_bytes,
                        checksum=checksum,
                        vg_id=vg_id,
                        shard_count=1,
                        shard_index=None,
                        block_size=None,
                        shard_profile=_archive_profile(request.mode),
                        parent_id=None,
                    )
                    instance = catalog.create_file_instance(
                        file_record_id=file_record.id,
                        barcode=barcode,
                        tape_path=tape_path,
                    )
                    catalog.mark_instance_archived(instance.id)
                    shard_record = catalog.create_file_record(
                        path=_shard_record_path(source_file, 0),
                        size_bytes=size_bytes,
                        checksum=checksum,
                        vg_id=vg_id,
                        shard_count=1,
                        shard_index=0,
                        block_size=None,
                        shard_profile=_archive_profile(request.mode),
                        parent_id=file_record.id,
                    )
                    shard_instance = catalog.create_file_instance(
                        file_record_id=shard_record.id,
                        barcode=barcode,
                        tape_path=tape_path,
                    )
                    catalog.mark_instance_archived(shard_instance.id)
                    shard_group_ids.append(str(uuid.uuid4()))
                    files_archived += 1
                    bytes_archived += size_bytes
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        finally:
            for mount in mounts.values():
                with suppress(Exception):
                    ltfs.unmount(mount)
            for handle in handles:
                slot_id = loaded_slots.get(handle.drive_id)
                if slot_id is not None:
                    with suppress(Exception):
                        execute_tape_request(
                            catalog,
                            library,
                            ltfs,
                            TapeOpRequest(
                                op_type=TapeOpType.UNLOAD,
                                barcode=handle.barcode,
                                drive_id=handle.drive_id,
                                slot_id=slot_id,
                                requested_by="sharded-archive",
                                job_id=job_id,
                            ),
                        )
            scheduler.release_drives(handles)

    return files_archived, bytes_archived


def _archive_block_stripe(
    source_file: Path,
    request: ShardedArchiveRequest,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    scratch_dir: Path,
    vg_id: str,
    shard_group_ids: list[str],
    job_id: str,
) -> None:
    plan = plan_block_stripe(
        source_file,
        request.lane_barcodes,
        "/block_stripe",
        block_size=request.block_size,
    )
    shard_group_ids.append(plan.shard_group_id)
    handles = scheduler.acquire_drives(request.lane_barcodes)
    mounts: dict[str, object] = {}
    loaded_slots: dict[int, int | None] = {}
    shard_dir = scratch_dir / plan.shard_group_id
    shard_dir.mkdir(parents=True, exist_ok=True)

    try:
        for handle in handles:
            drive_id, slot_id = _load_barcode(catalog, library, ltfs, handle, job_id)
            loaded_slots[drive_id] = slot_id
            mounts[handle.barcode] = ltfs.mount(handle.barcode, MountMode.READ_WRITE)

        shard_tmp_files: list[Path] = [Path()] * len(plan.shards)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan.shards)) as pool:
            future_map = {
                pool.submit(
                    write_shard_to_tempfile,
                    source_file,
                    spec.shard_index,
                    len(plan.shards),
                    request.block_size,
                    shard_dir,
                ): spec.shard_index
                for spec in plan.shards
            }
            for future in concurrent.futures.as_completed(future_map):
                shard_index = future_map[future]
                shard_tmp_files[shard_index] = future.result()

        def _write_shard(spec: ShardSpec, shard_tmp: Path) -> tuple[int, str, int]:
            checksum = compute_checksum(shard_tmp)
            mount = mounts[spec.barcode]
            ltfs.write_file(mount, shard_tmp, PurePosixPath(spec.tape_path))
            stat = ltfs.stat(mount, PurePosixPath(spec.tape_path))
            if stat.checksum_sha256 != checksum:
                raise ValueError(f"Shard {spec.shard_index} checksum mismatch")
            return spec.shard_index, checksum, shard_tmp.stat().st_size

        shard_checksums: list[str] = [""] * len(plan.shards)
        shard_sizes: list[int] = [0] * len(plan.shards)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan.shards)) as pool:
            futures = [
                pool.submit(_write_shard, spec, shard_tmp_files[spec.shard_index])
                for spec in plan.shards
            ]
            for future in concurrent.futures.as_completed(futures):
                shard_index, checksum, shard_size = future.result()
                shard_checksums[shard_index] = checksum
                shard_sizes[shard_index] = shard_size

        file_record = catalog.create_file_record(
            path=str(source_file),
            size_bytes=plan.file_size,
            checksum=plan.checksum_sha256,
            vg_id=vg_id,
            shard_count=len(plan.shards),
            shard_index=None,
            block_size=request.block_size,
            shard_profile=_archive_profile(request.mode),
            parent_id=None,
        )
        for spec in plan.shards:
            instance = catalog.create_file_instance(
                file_record_id=file_record.id,
                barcode=spec.barcode,
                tape_path=spec.tape_path,
            )
            catalog.mark_instance_archived(instance.id)
            shard_record = catalog.create_file_record(
                path=_shard_record_path(source_file, spec.shard_index),
                size_bytes=shard_sizes[spec.shard_index],
                checksum=shard_checksums[spec.shard_index],
                vg_id=vg_id,
                shard_count=len(plan.shards),
                shard_index=spec.shard_index,
                block_size=request.block_size,
                shard_profile=_archive_profile(request.mode),
                parent_id=file_record.id,
            )
            shard_instance = catalog.create_file_instance(
                file_record_id=shard_record.id,
                barcode=spec.barcode,
                tape_path=spec.tape_path,
            )
            catalog.mark_instance_archived(shard_instance.id)
    finally:
        for mount in mounts.values():
            with suppress(Exception):
                ltfs.unmount(mount)
        for handle in handles:
            slot_id = loaded_slots.get(handle.drive_id)
            if slot_id is not None:
                with suppress(Exception):
                    execute_tape_request(
                        catalog,
                        library,
                        ltfs,
                        TapeOpRequest(
                            op_type=TapeOpType.UNLOAD,
                            barcode=handle.barcode,
                            drive_id=handle.drive_id,
                            slot_id=slot_id,
                            requested_by="sharded-archive",
                            job_id=job_id,
                        ),
                    )
        scheduler.release_drives(handles)
        shutil.rmtree(shard_dir, ignore_errors=True)


def _load_barcode(
    catalog: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    handle: DriveHandle,
    job_id: str,
) -> tuple[int, int | None]:
    loaded_drive_id = library.find_drive_by_barcode(handle.barcode)
    if loaded_drive_id is not None:
        if loaded_drive_id != handle.drive_id:
            handle.drive_id = loaded_drive_id
        return loaded_drive_id, None

    inventory = library.inventory()
    slot_id = next(
        (
            slot.slot_id
            for slot in inventory.slots
            if slot.barcode is not None and slot.barcode.value == handle.barcode
        ),
        None,
    )
    if slot_id is None:
        raise ValueError(f"Barcode {handle.barcode} not found in any slot")
    execute_tape_request(
        catalog,
        library,
        ltfs,
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode=handle.barcode,
            drive_id=handle.drive_id,
            slot_id=slot_id,
            requested_by="sharded-archive",
            job_id=job_id,
        ),
        raise_on_failed=True,
    )
    return handle.drive_id, slot_id


def _make_scratch_dir(prefix: str) -> Path:
    scratch_dir = Path.cwd() / ".openblade-scratch" / f"{prefix}-{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=False)
    return scratch_dir
