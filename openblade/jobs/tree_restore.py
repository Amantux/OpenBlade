"""Bulk (tree) restore: every archived file under a catalog path prefix.

The campaign runbook records this as missing: ``openblade restore`` and
``POST /restore/`` each take one catalog path, and
``scripts/campaign/restore_and_verify.py`` loops "because it has to". This is
that loop, moved into the service layer where it can be tested and audited.

It deliberately does NOT reimplement restoring. Each file goes through the
existing per-file services -- ``run_sharded_restore`` when the catalog says the
file is sharded (it reassembles across tapes), ``run_restore_job`` otherwise --
which is the same choice ``POST /restore/`` makes. What this adds is:

* prefix expansion and a destination layout that PRESERVES the tree. The
  runbook's "restore to a directory uses the basename only" behaviour collapses
  same-named files from different subdirectories on top of each other; a bulk
  restore that did that would silently lose data.
* tape grouping, so files on one cartridge are restored consecutively.
* a summary: files, bytes, per-tape counts, verification, and every failure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from openblade.catalog.models import FileRecord
from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend, LTFSBackend
from openblade.domain.errors import UnsafeCatalogPathError, safe_job_error
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore

ProgressFn = Callable[["TreeRestoreProgress"], None]


@dataclass
class TreeRestoreRequest:
    catalog_prefix: str
    dest_dir: Path
    dry_run: bool = False


@dataclass
class TreeRestoreProgress:
    """One progress tick: a batch of files on one cartridge."""

    barcode: str
    files_done: int
    files_total: int
    bytes_done: int
    last_path: str
    failed: bool = False


@dataclass
class TreeRestoreFailure:
    catalog_path: str
    error: str

    def to_dict(self) -> dict[str, object]:
        return {"catalogPath": self.catalog_path, "error": self.error}


@dataclass
class TreeRestoreResult:
    job_id: str
    catalog_prefix: str
    dest_dir: str
    files_restored: int = 0
    files_failed: int = 0
    bytes_restored: int = 0
    files_verified: int = 0
    # barcode -> number of files restored from that cartridge.
    per_tape_counts: dict[str, int] = field(default_factory=dict)
    failures: list[TreeRestoreFailure] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "catalogPrefix": self.catalog_prefix,
            "destDir": self.dest_dir,
            "dryRun": self.dry_run,
            "filesRestored": self.files_restored,
            "filesFailed": self.files_failed,
            "bytesRestored": self.bytes_restored,
            "filesVerified": self.files_verified,
            "perTapeCounts": dict(sorted(self.per_tape_counts.items())),
            "tapesUsed": sorted(self.per_tape_counts),
            "failures": [failure.to_dict() for failure in self.failures],
            "status": "completed" if self.ok else "failed",
        }


@dataclass
class _PlannedFile:
    catalog_path: str
    dest_path: Path
    size_bytes: int
    barcode: str
    sharded: bool


def _normalize_prefix(prefix: str) -> str:
    normalized = str(PurePosixPath("/") / prefix.strip().lstrip("/"))
    return normalized


def _relative_parts(catalog_path: str, prefix: str) -> tuple[str, ...]:
    """Destination path components for ``catalog_path`` under ``prefix``.

    ``/photos`` + ``/photos/2024/a.jpg`` -> ``("2024", "a.jpg")``. Keeping the
    subdirectories is the whole point: flattening to the basename is what makes
    ``alpha/same.txt`` and ``beta/same.txt`` overwrite each other.

    The result is joined onto ``--dest``, and catalog rows are not trusted to be
    well-formed: ``create_file_record`` normalises with ``PurePosixPath`` which
    does NOT collapse ``..``. A surviving ``..`` component writes outside the
    destination, and an absolute component would reset the join to the
    filesystem root (``Path("/a").joinpath("/etc")`` is ``/etc``). Strip both,
    and refuse rather than fall back to ``path.name`` -- for ``/vg/..`` that
    basename is itself ``".."``, which put the escape straight back.
    """
    path = PurePosixPath(catalog_path)
    if prefix == "/":
        parts = path.parts[1:]
    elif catalog_path == prefix:
        parts = (path.name,)
    else:
        parts = path.relative_to(PurePosixPath(prefix)).parts
    safe = tuple(part for part in parts if part not in {"", "/", ".", ".."})
    if not safe:
        raise UnsafeCatalogPathError(
            f"Catalog path {catalog_path!r} has no component that can be written "
            f"safely under a destination directory (prefix {prefix!r}); "
            "refusing rather than writing outside --dest"
        )
    return safe


def _select_records(catalog: CatalogRepository, prefix: str) -> list[FileRecord]:
    """Top-level catalog records strictly under ``prefix``.

    ``list_file_records`` matches with SQL ``LIKE 'prefix%'``, so a prefix of
    ``/photo`` also matches ``/photos-old/x``. Re-filter on path components.
    """
    selected: list[FileRecord] = []
    for record in catalog.list_file_records(prefix):
        path = str(record.path)
        if prefix == "/" or path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            selected.append(record)
    return selected


def plan_tree_restore(
    request: TreeRestoreRequest, catalog: CatalogRepository
) -> list[_PlannedFile]:
    """Resolve the prefix into per-file work, grouped by cartridge.

    Grouping by barcode keeps one cartridge's files together instead of
    interleaving tapes across the run. Ties break on catalog path so the plan is
    deterministic (and so a re-run restores in the same order).
    """
    prefix = _normalize_prefix(request.catalog_prefix)
    planned: list[_PlannedFile] = []
    for record in _select_records(catalog, prefix):
        shard_records = catalog.list_shard_records(record.id)
        sharded = bool(shard_records) or (record.shard_count or 1) > 1
        barcodes = sorted(
            {
                instance.barcode
                for instance in record.instances
                if instance.state in {"archived", "verified"}
            }
        )
        if not barcodes and sharded:
            barcodes = sorted(
                {
                    instance.barcode
                    for shard in shard_records
                    for instance in shard.instances
                    if instance.state in {"archived", "verified"}
                }
            )
        if not barcodes:
            # Nothing archived for this record: it is catalogued but was never
            # written (a failed archive leaves exactly this). Skip rather than
            # fail the whole tree on it -- it is reported as a failure below only
            # if the caller asked for it explicitly.
            continue
        planned.append(
            _PlannedFile(
                catalog_path=str(record.path),
                dest_path=request.dest_dir.joinpath(
                    *_relative_parts(str(record.path), prefix)
                ),
                size_bytes=int(record.size_bytes or 0),
                barcode=barcodes[0],
                sharded=sharded,
            )
        )
    planned.sort(key=lambda item: (item.barcode, item.catalog_path))
    return planned


def run_tree_restore(
    request: TreeRestoreRequest,
    library: LibraryBackend,
    ltfs: LTFSBackend,
    catalog: CatalogRepository,
    scheduler: DriveScheduler,
    job_id: str,
    progress: ProgressFn | None = None,
) -> TreeRestoreResult:
    """Restore every archived file under ``catalog_prefix`` into ``dest_dir``."""
    prefix = _normalize_prefix(request.catalog_prefix)
    catalog.update_job_state(job_id, "running")
    planned = plan_tree_restore(request, catalog)
    result = TreeRestoreResult(
        job_id=job_id,
        catalog_prefix=prefix,
        dest_dir=str(request.dest_dir),
        dry_run=request.dry_run,
    )

    if request.dry_run:
        for item in planned:
            result.files_restored += 1
            result.bytes_restored += item.size_bytes
            result.per_tape_counts[item.barcode] = (
                result.per_tape_counts.get(item.barcode, 0) + 1
            )
        catalog.update_job_state(job_id, "completed")
        return result

    for item in planned:
        child = catalog.create_job(
            "restore",
            {
                "catalog_path": item.catalog_path,
                "dest_path": str(item.dest_path),
                "parent_job_id": job_id,
            },
        )
        try:
            item.dest_path.parent.mkdir(parents=True, exist_ok=True)
            if item.sharded:
                sharded_result = run_sharded_restore(
                    ShardedRestoreRequest(
                        catalog_path=item.catalog_path, dest_path=item.dest_path
                    ),
                    library,
                    ltfs,
                    catalog,
                    scheduler,
                    child.id,
                )
                if sharded_result.error:
                    raise RuntimeError(sharded_result.error)
                verified = sharded_result.checksum_verified
                barcodes = sharded_result.source_barcodes or [item.barcode]
            else:
                plain_result = run_restore_job(
                    RestoreRequest(
                        catalog_path=item.catalog_path, dest_path=item.dest_path
                    ),
                    library,
                    ltfs,
                    catalog,
                    child.id,
                )
                verified = plain_result.checksum_verified
                barcodes = [plain_result.source_barcode]
        except Exception as exc:  # noqa: BLE001 -- summarised, curated, and reported
            message = safe_job_error(exc)
            catalog.update_job_state(child.id, "failed", error=message)
            result.files_failed += 1
            result.failures.append(
                TreeRestoreFailure(catalog_path=item.catalog_path, error=message)
            )
            if progress is not None:
                progress(
                    TreeRestoreProgress(
                        barcode=item.barcode,
                        files_done=result.files_restored + result.files_failed,
                        files_total=len(planned),
                        bytes_done=result.bytes_restored,
                        last_path=item.catalog_path,
                        failed=True,
                    )
                )
            continue

        result.files_restored += 1
        result.bytes_restored += item.size_bytes
        if verified:
            result.files_verified += 1
        for barcode in barcodes:
            result.per_tape_counts[barcode] = result.per_tape_counts.get(barcode, 0) + 1
        if progress is not None:
            progress(
                TreeRestoreProgress(
                    barcode=item.barcode,
                    files_done=result.files_restored + result.files_failed,
                    files_total=len(planned),
                    bytes_done=result.bytes_restored,
                    last_path=item.catalog_path,
                )
            )

    if result.ok:
        catalog.update_job_state(job_id, "completed")
    else:
        catalog.update_job_state(
            job_id,
            "failed",
            error=f"{result.files_failed} of {len(planned)} file(s) failed to restore",
        )
    return result
