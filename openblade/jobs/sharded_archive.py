"""Sharded archive job: writes to multiple drives in parallel."""

from __future__ import annotations

import concurrent.futures
import logging
import os
import shutil
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.capacity import has_room_for
from openblade.domain.errors import StaleLeaseError, TapeFullError, safe_job_error
from openblade.domain.models import MountHandle, MountMode
from openblade.jobs.inventory import InventoryService
from openblade.jobs.reconcile import ensure_drive_reconciled
from openblade.jobs.scheduler import DriveHandle, DriveScheduler, JournalWriteError
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


def _stripe_tape_path(source_file: Path, source_root: Path) -> str:
    """On-tape location for a STRIPE-mode file, preserving the source tree.

    This used to be ``/stripe/{source_file.name}`` -- a single flat namespace
    keyed on the basename. Any two files with the same name in different source
    directories that landed on the same lane therefore wrote to the same place,
    and the second silently overwrote the first. Both were marked ``archived``.
    Demonstrated on the rig: ``alpha/same.txt`` and ``beta/same.txt`` both
    catalogued at ``OB0001L8:/stripe/same.txt``; restoring alpha returned beta's
    bytes and only the checksum verify caught it -- at restore time, long after
    the source was presumed safe. See docs/runbooks/real-data-campaign.md.

    Existing media are unaffected: restore reads ``file_instances.tape_path`` as
    stored, it does not recompute this.

    The result is always strictly under ``/stripe``. ``source_root`` arrives from
    the API body (``POST /archive/sharded {"source_path": ...}``) and pathlib does
    not normalise, so ``/data/../etc/passwd`` relative to ``/data`` yields
    ``../etc/passwd`` -- which ``write_file`` would join onto the mount point and
    escape it. Normalise lexically (never ``resolve()``: that touches the
    filesystem and follows symlinks) and then drop any component that could still
    climb.
    """
    file_path = PurePosixPath(os.path.normpath(str(source_file)))
    root = PurePosixPath(os.path.normpath(str(source_root)))
    if file_path == root:
        # source_path was the file itself: relative_to() returns Path(".") here
        # rather than raising, which would collapse the tape path to "/stripe".
        relative: PurePosixPath = PurePosixPath(file_path.name)
    else:
        try:
            relative = file_path.relative_to(root)
        except ValueError:
            relative = PurePosixPath(file_path.name)
    parts = [part for part in relative.parts if part not in {"", ".", "..", "/"}]
    if not parts:
        parts = [file_path.name]
    return str(PurePosixPath("/stripe", *parts))


def _archive_profile(mode: ShardMode) -> str:
    return mode.value


def _require_lane_room(ltfs: LTFSBackend, barcode: str, size_bytes: int) -> None:
    """Refuse to write to a lane that cannot hold ``size_bytes``.

    The sharded path has no tape selection at all -- lanes come straight from the
    request -- so unlike ``jobs/archive.py`` there is nothing to spill onto here
    (that gap is recorded in docs/runbooks/real-data-campaign.md and is not fixed
    by this change). What this does buy is that a full lane is a *capacity
    decision* with the tape named, routed through the one shared policy in
    ``openblade.domain.capacity``, instead of a raw ``OSError: [Errno 28]``
    surfacing from LTFS half way through a batch.
    """
    tape = ltfs.ensure_tape(barcode)
    if not has_room_for(int(tape.capacity_bytes), int(tape.used_bytes), size_bytes):
        raise TapeFullError(
            f"Tape {barcode} has no room for {size_bytes} more bytes "
            "(including the LTFS index reserve)"
        )


_JOB_ERROR_MAX_CHARS = 2000


def _summarize_errors(errors: list[str]) -> str | None:
    """Condense per-batch failures into one bounded string for ``jobs.error``.

    A sharded archive can fail hundreds of batches with the same cause, so the
    distinct messages are what carry information -- and the column has to stay a
    sane size. Returns None when there is nothing to report, so a clean run still
    clears the field.
    """
    if not errors:
        return None
    distinct: list[str] = []
    for message in errors:
        if message not in distinct:
            distinct.append(message)
    summary = f"{len(errors)} shard batch failure(s); {len(distinct)} distinct: " + " | ".join(
        distinct
    )
    if len(summary) > _JOB_ERROR_MAX_CHARS:
        summary = summary[: _JOB_ERROR_MAX_CHARS - 3] + "..."
    return summary


def run_sharded_archive(
    request: ShardedArchiveRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
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
                    errors.append(_job_error(exc))
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
    # `errors` used to be returned in ShardedArchiveResult and nowhere else. The
    # API route discards the result (it answers {job_id, status:"pending"}), so a
    # sharded archive that failed every batch surfaced as `failed_recoverable`
    # with `error: null` and no log line -- the first real-data run wrote 3 of
    # 1,073 files over ten minutes and said nothing about why. Persist a bounded
    # summary on the job, which is the one place an operator looks.
    catalog.update_job_state(job_id, state, _summarize_errors(errors))
    return ShardedArchiveResult(
        job_id=job_id,
        files_archived=files_archived,
        bytes_archived=bytes_archived,
        tapes_used=list(request.lane_barcodes),
        shard_group_ids=shard_group_ids,
        errors=errors,
    )


class TapePhysicalStateError(RuntimeError):
    """Unmount/unload failed after a write; physical state is unknown and needs reconciliation.

    Raised on the commit path so a dirty or unknown physical state BLOCKS marking the
    batch durable, instead of the failure being silently suppressed.
    """


def _clean_unmount_and_unload(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    mounts: dict[str, MountHandle],
    handles: list[DriveHandle],
    loaded_slots: dict[int, int | None],
    job_id: str,
) -> None:
    """Unmount every tape and unload every drive, raising on any failure.

    On success, entries are removed from ``mounts``/``loaded_slots`` so a subsequent
    best-effort ``finally`` cleanup does not act on already-released hardware. On any
    failure the errors are aggregated and raised as TapePhysicalStateError so the
    caller does NOT mark the batch archived.
    """
    failures: list[str] = []
    still_mounted: set[str] = set()
    for barcode, mount in list(mounts.items()):
        try:
            ltfs.unmount(mount)
            mounts.pop(barcode, None)
        except Exception as exc:  # noqa: BLE001 - aggregated and re-raised below
            logger.exception("job %s: unmount of %s failed", job_id, barcode)
            failures.append(f"unmount {barcode}: {type(exc).__name__}")
            still_mounted.add(barcode)
    for handle in handles:
        slot_id = loaded_slots.get(handle.physical)
        if slot_id is None:
            continue
        if handle.barcode in still_mounted:
            # Never unload while LTFS is mounted or dirty: a failed unmount leaves
            # the drive in an unknown state, which is reported, not forced.
            failures.append(f"unload {handle.barcode}: skipped, unmount failed")
            continue
        try:
            # raise_on_failed=True is essential: execute_tape_request otherwise
            # SWALLOWS an unload failure (returns a FAILED record) and the batch would
            # commit with a tape stuck in a drive. pop only on genuine success.
            execute_tape_request(
                catalog,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.UNLOAD,
                    barcode=handle.barcode,
                    drive_id=handle.physical,
                    slot_id=slot_id,
                    requested_by="sharded-archive",
                    job_id=job_id,
                ),
                raise_on_failed=True,
            )
            loaded_slots.pop(handle.physical, None)
        except Exception as exc:  # noqa: BLE001 - aggregated and re-raised below
            logger.exception("job %s: unload of %s failed", job_id, handle.barcode)
            failures.append(f"unload {handle.barcode}: {type(exc).__name__}")
    if failures:
        raise TapePhysicalStateError(
            "shards written but physical state is unknown (reconcile required): "
            + "; ".join(failures)
        )


def _log_fenced_out(handles: list[DriveHandle], job_id: str) -> None:
    for handle in handles:
        logger.error(
            "job %s lost its lease on drive %d (%s); leaving the drive untouched — "
            "physical state unknown, reconcile before reuse",
            job_id,
            handle.physical,
            handle.barcode,
        )


PHYSICAL_STATE_UNKNOWN = "physical state unknown — reconcile before reuse"


def _job_error(exc: Exception) -> str:
    """jobs.error text: the typed physical-state message, else safe_job_error."""
    if isinstance(exc, TapePhysicalStateError):
        return PHYSICAL_STATE_UNKNOWN
    return safe_job_error(exc)


def _journal_failure(
    catalog: CatalogRepository, job_id: str, event: str, detail: dict[str, object]
) -> None:
    """Journal on an error path without letting a broken session mask the real error."""
    try:
        catalog.journal(job_id, event, detail)
    except Exception:  # noqa: BLE001 - evidence only; the original exception is what the caller reports
        logger.exception("job %s: could not journal %s; rolling the session back", job_id, event)
        with suppress(Exception):
            catalog.session.rollback()


def record_physical_state_unknown(
    catalog: CatalogRepository,
    job_id: str,
    op: str,
    barcode: str,
    drive: int | None,
    *,
    handles: Sequence[DriveHandle],
) -> None:
    """Log + journal a cleanup op that raised; the drive must be reconciled before reuse.

    Fail closed: the row is written via ``journal_durable`` (fresh session, retried).
    If that still fails, the matching handle is flagged ``hold_unjournaled`` so
    ``release_drives`` keeps its lease and then raises ``JournalWriteError`` --
    after every other drive was cleaned and released. With no matching handle
    there is no lease to keep, so the error is raised here.
    """
    logger.exception(
        "job %s: %s of %s (drive %s) failed during cleanup; %s",
        job_id,
        op,
        barcode,
        drive,
        PHYSICAL_STATE_UNKNOWN,
    )
    try:
        catalog.journal_durable(
            job_id, "physical_state_unknown", {"op": op, "barcode": barcode, "drive": drive}
        )
    except Exception:  # noqa: BLE001 - any journal failure must fail closed, never pass
        matched = [
            h
            for h in handles
            if h.barcode == barcode or (drive is not None and drive in (h.drive_id, h.physical))
        ]
        logger.exception(
            "job %s: could not journal physical_state_unknown for %s (drive %s); keeping its lease",
            job_id,
            barcode,
            drive,
        )
        if not matched:
            raise JournalWriteError(
                f"physical_state_unknown for {barcode} could not be journaled"
            ) from None
        for handle in matched:
            handle.hold_unjournaled = True


def _best_effort_unmount_and_unload(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    mounts: dict[str, MountHandle],
    handles: list[DriveHandle],
    loaded_slots: dict[int, int | None],
    job_id: str,
    errors: list[str] | None,
) -> None:
    """Error-path cleanup for a job that still owns its drives: unmount, then unload.

    Every op is attempted even if an earlier one raised. A failure is logged and
    journaled as ``physical_state_unknown``; it becomes the job error only when
    ``errors`` is empty (the original lane error wins). ``errors=None`` means an
    exception is already propagating and will become the job error.
    """
    drive_by_barcode = {handle.barcode: handle.physical for handle in handles}
    failed = False
    still_mounted: set[str] = set()
    for barcode, mount in mounts.items():
        try:
            ltfs.unmount(mount)
        except Exception:  # noqa: BLE001 - recorded as physical_state_unknown, cleanup continues
            failed = True
            still_mounted.add(barcode)
            record_physical_state_unknown(
                catalog,
                job_id,
                "unmount",
                barcode,
                drive_by_barcode.get(barcode),
                handles=handles,
            )
    for handle in handles:
        slot_id = loaded_slots.get(handle.physical)
        if slot_id is None:
            continue
        if handle.barcode in still_mounted:
            # Never unload while LTFS is mounted or dirty; the unmount failure above
            # is already journaled as physical_state_unknown for this drive.
            continue
        try:
            execute_tape_request(
                catalog,
                library,
                ltfs,
                TapeOpRequest(
                    op_type=TapeOpType.UNLOAD,
                    barcode=handle.barcode,
                    drive_id=handle.physical,
                    slot_id=slot_id,
                    requested_by="sharded-archive",
                    job_id=job_id,
                ),
            )
        except Exception:  # noqa: BLE001 - recorded as physical_state_unknown, cleanup continues
            failed = True
            record_physical_state_unknown(
                catalog, job_id, "unload", handle.barcode, handle.physical, handles=handles
            )
    if failed and errors is not None and not errors:
        errors.append(PHYSICAL_STATE_UNKNOWN)


def _mark_verifying(catalog: CatalogRepository, job_id: str, staged_ids: list[str]) -> None:
    catalog.journal(job_id, "verify_started", {"instances": len(staged_ids)})
    catalog.mark_instances_verifying(staged_ids)
    catalog.journal(job_id, "verify_finished", {"instances": len(staged_ids)})


def _commit_staged(
    catalog: CatalogRepository,
    library: LibraryBackend,
    job_id: str,
    handles: list[DriveHandle],
    staged_ids: list[str],
) -> None:
    """The atomic commit: inventory reconciled, then ONE mark_instances_archived."""
    inventory = InventoryService(library).snapshot()  # SAFETY_003: via service
    seen = {str(slot.barcode) for slot in inventory.slots if slot.barcode is not None}
    seen |= {str(drive.barcode) for drive in inventory.drives if drive.barcode is not None}
    missing = sorted({handle.barcode for handle in handles} - seen)
    if missing:
        logger.error("job %s: barcodes %s not in any slot or drive after unload", job_id, missing)
        raise TapePhysicalStateError(f"inventory does not show {', '.join(missing)}")
    catalog.mark_instances_archived(staged_ids)
    catalog.journal(job_id, "committed", {"instances": len(staged_ids)})


def _archive_stripe(
    files: list[Path],
    request: ShardedArchiveRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
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
        catalog.journal(job_id, "lease_acquired", {"barcodes": batch_barcodes})
        mounts: dict[str, MountHandle] = {}
        loaded_slots: dict[int, int | None] = {}
        # Instances are created STAGING before their first write and stay invisible
        # until the WHOLE batch has verified, every tape cleanly unmounted and the
        # inventory reconciled — then ONE mark_instances_archived (atomic commit).
        staged_ids: list[str] = []
        batch_bytes = 0
        fenced_out = False
        try:
            for handle in handles:
                drive_id, slot_id = _load_barcode(catalog, library, ltfs, handle, job_id)
                loaded_slots[drive_id] = slot_id
            scheduler.record_physical_drives(handles)
            for handle in handles:
                scheduler.verify(handle)  # fencing: never mount RW on a stale lease
                mounts[handle.barcode] = ltfs.mount(handle.barcode, MountMode.READ_WRITE)

            def _write_one(
                source_file: Path,
                barcode: str,
                mount: MountHandle,
                tape_path: str,
                checksum: str,
            ) -> None:
                _require_lane_room(ltfs, barcode, source_file.stat().st_size)
                ltfs.write_file(mount, source_file, PurePosixPath(tape_path))
                stat = ltfs.stat(mount, PurePosixPath(tape_path))
                if stat.checksum_sha256 != checksum:
                    raise ValueError(f"Checksum mismatch: {source_file.name}")

            # Stage every instance (main thread: the catalog session is not
            # thread-safe) BEFORE the first write, so a crash mid-write leaves
            # STAGING rows that recovery can find, never ARCHIVED ones.
            planned: list[tuple[Path, str, str, str]] = []
            for source_file, barcode in batch:
                tape_path = _stripe_tape_path(source_file, request.source_path)
                checksum = compute_checksum(source_file)
                size_bytes = source_file.stat().st_size
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
                instance = catalog.create_staged_instance(
                    job_id, file_record.id, barcode, tape_path
                )
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
                shard_instance = catalog.create_staged_instance(
                    job_id, shard_record.id, barcode, tape_path
                )
                staged_ids.extend((instance.id, shard_instance.id))
                batch_bytes += size_bytes
                planned.append((source_file, barcode, tape_path, checksum))
                shard_group_ids.append(str(uuid.uuid4()))

            for handle in handles:
                scheduler.verify(handle)  # fencing: before the shard write batch
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = [
                    pool.submit(_write_one, source_file, barcode, mounts[barcode], path, csum)
                    for source_file, barcode, path, csum in planned
                ]
                for future in concurrent.futures.as_completed(futures):
                    future.result()
            # Every lane wrote and read back (stat checksum) its shard.
            _mark_verifying(catalog, job_id, staged_ids)

            # Every shard in the batch wrote and checksum-verified. Cleanly unmount and
            # unload BEFORE commit; a failed unmount raises and blocks the commit.
            scheduler.heartbeat(handles)
            for handle in handles:
                scheduler.verify(handle)  # fencing: before unmount/unload + commit
            _clean_unmount_and_unload(catalog, library, ltfs, mounts, handles, loaded_slots, job_id)
            _commit_staged(catalog, library, job_id, handles, staged_ids)
            files_archived += len(planned)
            bytes_archived += batch_bytes
        except StaleLeaseError:
            # Fenced out: abort the whole job and leave the hardware ALONE. The
            # drive may already be loaded + mounted by whoever holds the lease now,
            # so unmount/unload here could eject their tape mid-write. Physical
            # state is unknown -> reconcile (recovery report / inventory).
            fenced_out = True
            _journal_failure(catalog, job_id, "fenced_out", {"barcodes": batch_barcodes})
            raise
        except Exception as exc:  # noqa: BLE001
            # Staged instances remain PENDING -> not exposed as archived; resumable.
            # This branch had no logging at all, so a batch that failed left no
            # trace anywhere except a list the caller throws away.
            logger.warning(
                "stripe batch failed on %s: %s: %s",
                ",".join(batch_barcodes),
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            _journal_failure(catalog, job_id, "failed", {"error": type(exc).__name__})
            errors.append(_job_error(exc))
        finally:
            if fenced_out:
                _log_fenced_out(handles, job_id)
            else:
                _best_effort_unmount_and_unload(
                    catalog, library, ltfs, mounts, handles, loaded_slots, job_id, errors
                )
            scheduler.release_drives(handles)

    return files_archived, bytes_archived


def _archive_block_stripe(
    source_file: Path,
    request: ShardedArchiveRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
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
    catalog.journal(job_id, "lease_acquired", {"barcodes": list(request.lane_barcodes)})
    mounts: dict[str, MountHandle] = {}
    loaded_slots: dict[int, int | None] = {}
    shard_dir = scratch_dir / plan.shard_group_id
    shard_dir.mkdir(parents=True, exist_ok=True)
    fenced_out = False

    try:
        for handle in handles:
            drive_id, slot_id = _load_barcode(catalog, library, ltfs, handle, job_id)
            loaded_slots[drive_id] = slot_id
        scheduler.record_physical_drives(handles)
        for handle in handles:
            scheduler.verify(handle)  # fencing: never mount RW on a stale lease
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

        shard_checksums = [compute_checksum(path) for path in shard_tmp_files]
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
        # Stage records/instances STAGING before the first shard write (main thread:
        # the catalog session is not thread-safe). Nothing is ARCHIVED until every
        # lane verified, every tape cleanly unmounted and inventory reconciled.
        staged: list[str] = []
        for spec in plan.shards:
            instance = catalog.create_staged_instance(
                job_id, file_record.id, spec.barcode, spec.tape_path
            )
            shard_record = catalog.create_file_record(
                path=_shard_record_path(source_file, spec.shard_index),
                size_bytes=shard_tmp_files[spec.shard_index].stat().st_size,
                checksum=shard_checksums[spec.shard_index],
                vg_id=vg_id,
                shard_count=len(plan.shards),
                shard_index=spec.shard_index,
                block_size=request.block_size,
                shard_profile=_archive_profile(request.mode),
                parent_id=file_record.id,
            )
            shard_instance = catalog.create_staged_instance(
                job_id, shard_record.id, spec.barcode, spec.tape_path
            )
            staged.extend((instance.id, shard_instance.id))

        def _write_shard(spec: ShardSpec, shard_tmp: Path) -> tuple[int, str, int]:
            _require_lane_room(ltfs, spec.barcode, shard_tmp.stat().st_size)
            checksum = shard_checksums[spec.shard_index]
            mount = mounts[spec.barcode]
            ltfs.write_file(mount, shard_tmp, PurePosixPath(spec.tape_path))
            stat = ltfs.stat(mount, PurePosixPath(spec.tape_path))
            if stat.checksum_sha256 != checksum:
                raise ValueError(f"Shard {spec.shard_index} checksum mismatch")
            return spec.shard_index, checksum, shard_tmp.stat().st_size

        for handle in handles:
            scheduler.verify(handle)  # fencing: before the shard write batch
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(plan.shards)) as pool:
            futures = [
                pool.submit(_write_shard, spec, shard_tmp_files[spec.shard_index])
                for spec in plan.shards
            ]
            for shard_future in concurrent.futures.as_completed(futures):
                shard_future.result()
        # Every lane wrote and read back (stat checksum) its shard.
        _mark_verifying(catalog, job_id, staged)

        scheduler.heartbeat(handles)
        for handle in handles:
            scheduler.verify(handle)  # fencing: before unmount/unload + commit
        # Clean unmount/unload BEFORE commit; a failed unmount raises and blocks it.
        _clean_unmount_and_unload(catalog, library, ltfs, mounts, handles, loaded_slots, job_id)
        _commit_staged(catalog, library, job_id, handles, staged)
    except StaleLeaseError:
        # Fenced out: abort and leave the hardware alone (see _archive_stripe).
        fenced_out = True
        _journal_failure(catalog, job_id, "fenced_out", {"barcodes": list(request.lane_barcodes)})
        raise
    except Exception as exc:
        _journal_failure(catalog, job_id, "failed", {"error": type(exc).__name__})
        raise
    finally:
        if fenced_out:
            _log_fenced_out(handles, job_id)
        else:
            _best_effort_unmount_and_unload(
                catalog, library, ltfs, mounts, handles, loaded_slots, job_id, None
            )
        scheduler.release_drives(handles)
        shutil.rmtree(shard_dir, ignore_errors=True)


def _load_barcode(
    catalog: CatalogRepository,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    handle: DriveHandle,
    job_id: str,
) -> tuple[int, int | None]:
    loaded_drive_id = library.find_drive_by_barcode(handle.barcode)
    if loaded_drive_id is not None:
        ensure_drive_reconciled(catalog, loaded_drive_id, handle.barcode)
        # Cartridge is already in a drive. Record it as the PHYSICAL drive without
        # touching the scheduler lock key (handle.drive_id) — mutating that key would
        # leak the reserved drive and free a drive the scheduler never held.
        if loaded_drive_id != handle.drive_id:
            handle.physical_drive_id = loaded_drive_id
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
    ensure_drive_reconciled(catalog, handle.drive_id, handle.barcode)
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
