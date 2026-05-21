"""NAS ingest executor. Executes approved archive plans against the simulator."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, Field

from openblade.domain.models import MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.nas.archive_lifecycle import ArchiveLifecycleManager, ArchiveLifecycleResult, DatasetArchiveResult
from openblade.nas.catalog_shard import CatalogShardWriter
from openblade.nas.ltfs_manifest import TapeMetadataWriter
from openblade.nas.manifest_validator import ManifestValidator, VersionedManifestWriter
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.service import NasService
from openblade.nas.types import (
    ArchivePlan,
    CacheDriveConfig,
    DatasetStatus,
    IngestMode,
    NasDataset,
    NasFileRecord,
    NasFileState,
    SourceStreamConfig,
    TapeAssignment,
)
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

logger = logging.getLogger(__name__)


class IngestJob(BaseModel):
    """Tracks state of a running ingest operation."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    dataset_id: str
    plan: ArchivePlan
    status: DatasetStatus = DatasetStatus.ARCHIVING
    files_processed: int = 0
    files_failed: int = 0
    bytes_written: int = 0
    current_tape: str | None = None
    errors: list[str] = Field(default_factory=list)
    cancel_requested: bool = False
    partial_success: bool = False


class StartIngestResponse(BaseModel):
    job_id: str
    dataset_id: str
    status: str


ArchivePlanStore: dict[str, ArchivePlan] = {}
IngestJobStore: dict[str, IngestJob] = {}
_STORE_LOCK = RLock()


@dataclass(frozen=True)
class _PreparedFile:
    relative_path: str
    source_path: str
    size_bytes: int
    checksum_sha256: str
    mtime: str
    cache_path: str | None = None


class IngestCancelledError(RuntimeError):
    pass


class _BaseIngest:
    def __init__(
        self,
        *,
        job: IngestJob,
        dataset: NasDataset,
        service: NasService,
        library: MockLibraryBackend,
        ltfs: MockLTFSBackend,
    ) -> None:
        self.job = job
        self.dataset = dataset
        self.service = service
        self.library = library
        self.ltfs = ltfs
        self._file_record_ids = {
            record.relative_path: record.id for record in service.list_file_records(dataset.id)
        }
        self._prepared_files: dict[str, _PreparedFile] = {}
        self._tape_paths: dict[str, str] = {}
        self._dataset_archive_result: DatasetArchiveResult | None = None

    @staticmethod
    def _prepared_file_key(relative_path: str, barcode: str) -> str:
        return f"{barcode}:{relative_path}"

    def run(self) -> IngestJob:
        try:
            self._prepared_files = self._prepare_files()
            self.job.errors.extend(self._preflight(self.job))
            for assignment in self.job.plan.tape_assignments:
                self._check_cancelled()
                self._write_assignment(assignment)
            self._complete_dataset_archive()
            self._finalize_job()
        except IngestCancelledError as exc:
            self._mark_dataset_cancelled(str(exc))
        except Exception as exc:
            self._mark_dataset_failed(str(exc))
        finally:
            self.job.current_tape = None
        return self.job

    def _prepare_files(self) -> dict[str, _PreparedFile]:
        raise NotImplementedError

    def _preflight(self, job: IngestJob) -> list[str]:
        del job
        return []

    def _initial_file_status(self) -> NasFileState:
        return NasFileState.ONLINE_CACHED

    def _record_cache_path(self, relative_path: str) -> str | None:
        return None

    def _check_cancelled(self) -> None:
        if self.job.cancel_requested:
            raise IngestCancelledError(f"Cancelled by user: ingest job {self.job.job_id}")

    def _write_assignment(self, assignment: TapeAssignment) -> None:
        self.job.current_tape = assignment.barcode
        drive_id, slot_id = _load_if_needed(self.library, assignment.barcode)
        self._ensure_formatted(assignment.barcode)
        handle = self.ltfs.mount(assignment.barcode, MountMode.READ_WRITE)
        try:
            for relative_path in assignment.files:
                self._check_cancelled()
                prepared_key = self._prepared_file_key(relative_path, assignment.barcode)
                prepared = self._prepared_files.get(prepared_key)
                if prepared is None:
                    prepared = self._prepare_single_file(relative_path, assignment)
                    self._prepared_files[prepared_key] = prepared
                try:
                    tape_path = self._simulate_tape_write(
                        handle=handle,
                        barcode=assignment.barcode,
                        relative_path=relative_path,
                        prepared=prepared,
                    )
                    self._verify_tape_copy(handle, tape_path, prepared)
                    file_record = self._upsert_file_record(
                        prepared,
                        tape_barcode=assignment.barcode,
                        status=self._initial_file_status(),
                    )
                    lifecycle_result = _run_lifecycle_for_file(
                        file_record,
                        assignment.barcode,
                        str(tape_path),
                        self.job.plan.policy_name or "",
                        self.service.repository,
                        self.ltfs,
                    )
                    if not lifecycle_result.success:
                        raise RuntimeError(
                            "; ".join(lifecycle_result.errors)
                            or f"archive lifecycle failed for {prepared.relative_path}"
                        )
                    self._tape_paths[file_record.id] = str(tape_path)
                    self.job.files_processed += 1
                    self.job.bytes_written += prepared.size_bytes
                except Exception as exc:
                    self.job.files_failed += 1
                    self.job.errors.append(f"{relative_path}: {exc}")
                    self._upsert_file_record(
                        prepared,
                        tape_barcode=assignment.barcode,
                        status=NasFileState.FAILED,
                    )
        finally:
            self.ltfs.unmount(handle)
            if slot_id is not None:
                self.library.unload(drive_id, slot_id)

    def _prepare_single_file(self, relative_path: str, assignment: TapeAssignment) -> _PreparedFile:
        source_path = self._resolve_source_path(relative_path)
        size_bytes = self._resolve_size_bytes(relative_path, assignment)
        return _PreparedFile(
            relative_path=relative_path,
            source_path=source_path,
            size_bytes=size_bytes,
            checksum_sha256=self._checksum_for_file(relative_path, assignment.barcode),
            mtime=_utcnow_iso(),
            cache_path=self._record_cache_path(relative_path),
        )

    def _resolve_source_path(self, relative_path: str) -> str:
        source_root = self.job.plan.source_path or ""
        if relative_path.startswith("/"):
            return relative_path
        if source_root:
            return str(Path(source_root) / relative_path)
        return relative_path

    def _resolve_size_bytes(self, relative_path: str, assignment: TapeAssignment) -> int:
        del relative_path
        if assignment.files and assignment.estimated_bytes > 0:
            return max(1, assignment.estimated_bytes // len(assignment.files))
        if self.job.plan.total_files > 0 and self.job.plan.total_bytes > 0:
            return max(1, self.job.plan.total_bytes // self.job.plan.total_files)
        return 1024

    def _checksum_for_file(self, relative_path: str, barcode: str) -> str:
        """Return sha256 of the bytes that _simulate_tape_write will write for this file."""
        del barcode
        source_path = self._resolve_source_path(relative_path)
        return hashlib.sha256(b"simulated:" + source_path.encode()).hexdigest()

    def _ensure_formatted(self, barcode: str) -> None:
        tape = self.ltfs.ensure_tape(barcode)
        if tape.formatted:
            return
        self.ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )
        self.service.update_cartridge(
            barcode,
            volume_group_id=self.dataset.volume_group_id,
            used_bytes=0,
            capacity_bytes=tape.capacity_bytes,
            formatted=True,
        )

    def _simulate_tape_write(
        self,
        *,
        handle,
        barcode: str,
        relative_path: str,
        prepared: _PreparedFile,
    ) -> PurePosixPath:
        tape_path = PurePosixPath("/") / self.dataset.name / prepared.relative_path
        operation_checksum = hashlib.sha256(
            f"{prepared.relative_path}:{barcode}:{_utcnow_iso()}".encode()
        ).hexdigest()
        logger.info(
            "simulated nas ingest tape write",
            extra={
                "job_id": self.job.job_id,
                "dataset_id": self.dataset.id,
                "barcode": barcode,
                "relative_path": relative_path,
                "operation_checksum": operation_checksum,
            },
        )
        self.ltfs.write_bytes(
            handle,
            tape_path,
            b"simulated:" + prepared.source_path.encode(),
            size_bytes=prepared.size_bytes,
            checksum_sha256=prepared.checksum_sha256,
        )
        tape = self.ltfs.ensure_tape(barcode)
        self.service.update_cartridge(
            barcode,
            volume_group_id=self.dataset.volume_group_id,
            used_bytes=tape.used_bytes,
            capacity_bytes=tape.capacity_bytes,
            formatted=tape.formatted,
        )
        return tape_path

    def _verify_tape_copy(
        self,
        handle,
        tape_path: PurePosixPath,
        prepared: _PreparedFile,
    ) -> None:
        if not self.job.plan.verify_after_archive:
            return
        stat = self.ltfs.stat(handle, tape_path)
        if stat.checksum_sha256 != prepared.checksum_sha256 or stat.size_bytes != prepared.size_bytes:
            raise RuntimeError(f"verification failed for {prepared.relative_path}")

    def _upsert_file_record(
        self,
        prepared: _PreparedFile,
        *,
        tape_barcode: str | None,
        status: NasFileState,
    ) -> NasFileRecord:
        record_id = self._file_record_ids.get(prepared.relative_path)
        model = NasFileRecord(
            id=record_id or str(uuid4()),
            dataset_id=self.dataset.id,
            pool_id=self.dataset.pool_id,
            relative_path=prepared.relative_path,
            source_path=prepared.source_path,
            size_bytes=prepared.size_bytes,
            mtime=prepared.mtime,
            checksum_sha256=prepared.checksum_sha256,
            tape_barcode=tape_barcode,
            status=status,
            cache_path=prepared.cache_path,
        )
        saved = self.service.upsert_file_record(model)
        self._file_record_ids[prepared.relative_path] = saved.id
        return saved

    def _complete_dataset_archive(self) -> None:
        manager = _build_archive_lifecycle_manager(self.service.repository, self.ltfs)
        self._dataset_archive_result = manager.complete_dataset_archive(
            self.dataset.id,
            self.service.list_file_records(self.dataset.id),
            "",
            self._tape_paths,
            self.job.plan.policy_name or "",
        )
        self.job.errors.extend(self._dataset_archive_result.errors)

    def _finalize_job(self) -> None:
        if self.job.files_failed and self.job.files_processed == 0:
            self._mark_dataset_failed("All files failed during ingest")
            return
        if self.job.files_failed:
            self.job.partial_success = True
            self._mark_dataset_failed(
                f"Archived with partial success: {self.job.files_processed} succeeded, "
                f"{self.job.files_failed} failed"
            )
            return
        if self._dataset_archive_result is None or not self._dataset_archive_result.dataset_marked_archived:
            self._mark_dataset_failed("Dataset archive lifecycle did not complete")
            return
        dataset = self.service.get_dataset(self.dataset.id)
        assert dataset is not None
        self.dataset = dataset
        self.job.status = DatasetStatus.ARCHIVED

    def _mark_dataset_archived(self) -> None:
        dataset = self.service.get_dataset(self.dataset.id)
        assert dataset is not None
        updated = dataset.model_copy(
            update={
                "status": DatasetStatus.ARCHIVED,
                "copies_completed": dataset.copies_completed + 1,
                "tape_set": _ordered_unique([assignment.barcode for assignment in self.job.plan.tape_assignments]),
                "shard_map": {
                    assignment.barcode: list(assignment.files)
                    for assignment in self.job.plan.tape_assignments
                },
            }
        )
        self.dataset = self.service.upsert_dataset(updated)
        self.job.status = DatasetStatus.ARCHIVED

    def _mark_dataset_failed(self, error: str) -> None:
        if error not in self.job.errors:
            self.job.errors.append(error)
        dataset = self.service.get_dataset(self.dataset.id)
        if dataset is not None:
            self.dataset = self.service.upsert_dataset(
                dataset.model_copy(update={"status": DatasetStatus.FAILED})
            )
        self.job.status = DatasetStatus.FAILED

    def _mark_dataset_cancelled(self, error: str) -> None:
        if error not in self.job.errors:
            self.job.errors.append(error)
        dataset = self.service.get_dataset(self.dataset.id)
        if dataset is not None:
            self.dataset = self.service.upsert_dataset(
                dataset.model_copy(update={"status": DatasetStatus.CANCELLED})
            )
        self.job.status = DatasetStatus.CANCELLED


class CacheDriveIngest(_BaseIngest):
    """Execute cache-drive ingest against the simulator tape layer."""

    def __init__(
        self,
        *,
        job: IngestJob,
        dataset: NasDataset,
        service: NasService,
        library: MockLibraryBackend,
        ltfs: MockLTFSBackend,
        cache_drive: CacheDriveConfig,
    ) -> None:
        super().__init__(job=job, dataset=dataset, service=service, library=library, ltfs=ltfs)
        self.cache_drive = cache_drive

    def _prepare_files(self) -> dict[str, _PreparedFile]:
        prepared: dict[str, _PreparedFile] = {}
        initialized_paths: set[str] = set()
        for assignment in self.job.plan.tape_assignments:
            for relative_path in assignment.files:
                prepared_key = self._prepared_file_key(relative_path, assignment.barcode)
                prepared[prepared_key] = self._prepare_single_file(relative_path, assignment)
                if relative_path in initialized_paths:
                    continue
                initialized_paths.add(relative_path)
                self._upsert_file_record(
                    prepared[prepared_key],
                    tape_barcode=None,
                    status=self._initial_file_status(),
                )
        return prepared

    def _record_cache_path(self, relative_path: str) -> str | None:
        return str(Path(self.cache_drive.root_path) / relative_path)


class SourceStreamIngest(_BaseIngest):
    """Execute source-stream ingest against the simulator tape layer."""

    def __init__(
        self,
        *,
        job: IngestJob,
        dataset: NasDataset,
        service: NasService,
        library: MockLibraryBackend,
        ltfs: MockLTFSBackend,
        config: SourceStreamConfig,
    ) -> None:
        super().__init__(job=job, dataset=dataset, service=service, library=library, ltfs=ltfs)
        self.config = config

    def _prepare_files(self) -> dict[str, _PreparedFile]:
        prepared: dict[str, _PreparedFile] = {}
        for assignment in self.job.plan.tape_assignments:
            for relative_path in assignment.files:
                prepared_key = self._prepared_file_key(relative_path, assignment.barcode)
                prepared[prepared_key] = self._prepare_single_file(relative_path, assignment)
        return prepared

    def _preflight(self, job: IngestJob) -> list[str]:
        """Simulate source preflight check. Returns list of warnings."""
        warnings = []
        for assignment in job.plan.tape_assignments:
            for filepath in assignment.files:
                if not job.plan.source_path:
                    warnings.append(
                        f"No source_path set; streaming {filepath} without source validation"
                    )
        return warnings


def register_archive_plan(plan: ArchivePlan) -> ArchivePlan:
    with _STORE_LOCK:
        ArchivePlanStore[plan.plan_id] = plan
    return plan


def get_archive_plan(plan_id: str) -> ArchivePlan | None:
    with _STORE_LOCK:
        return ArchivePlanStore.get(plan_id)


def start_ingest_job(
    *,
    plan: ArchivePlan,
    dataset_name: str,
    pool_id: str | None,
    nas_service: NasService,
    cache_drive_id: str | None = None,
) -> IngestJob:
    del cache_drive_id
    dataset = nas_service.upsert_dataset(
        NasDataset(
            pool_id=pool_id,
            name=dataset_name,
            source_path=plan.source_path,
            policy_id=plan.policy_name,
            ingest_mode=plan.ingest_mode,
            volume_group_id=plan.volume_group,
            file_count=plan.total_files,
            total_bytes=plan.total_bytes,
            status=DatasetStatus.ARCHIVING,
        )
    )
    job = IngestJob(dataset_id=dataset.id, plan=plan, status=DatasetStatus.ARCHIVING)
    with _STORE_LOCK:
        IngestJobStore[job.job_id] = job
    return job


def get_ingest_job(job_id: str) -> IngestJob | None:
    with _STORE_LOCK:
        return IngestJobStore.get(job_id)


def cancel_ingest_job(job_id: str) -> bool:
    with _STORE_LOCK:
        job = IngestJobStore.get(job_id)
        if job is None:
            return False
        job.cancel_requested = True
        return True


def clear_ingest_state() -> None:
    with _STORE_LOCK:
        ArchivePlanStore.clear()
        IngestJobStore.clear()


def run_ingest_job(
    job_id: str,
    *,
    nas_service: NasService,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    cache_drive_id: str | None = None,
) -> IngestJob:
    job = get_ingest_job(job_id)
    if job is None:
        raise KeyError(f"Unknown ingest job {job_id}")
    dataset = nas_service.get_dataset(job.dataset_id)
    if dataset is None:
        raise KeyError(f"Unknown dataset {job.dataset_id}")
    if job.plan.ingest_mode is IngestMode.CACHE_DRIVE:
        if cache_drive_id is None:
            raise ValueError("cache_drive_id is required for cache-drive ingest")
        cache_drive = nas_service.get_cache_drive(cache_drive_id)
        if cache_drive is None:
            raise ValueError(f"Cache drive {cache_drive_id} not found")
        executor = CacheDriveIngest(
            job=job,
            dataset=dataset,
            service=nas_service,
            library=library,
            ltfs=ltfs,
            cache_drive=cache_drive,
        )
    else:
        executor = SourceStreamIngest(
            job=job,
            dataset=dataset,
            service=nas_service,
            library=library,
            ltfs=ltfs,
            config=nas_service.get_source_stream_config(),
        )
    return executor.run()


def _build_archive_lifecycle_manager(
    repo,
    backend: MockLTFSBackend,
) -> ArchiveLifecycleManager:
    metadata_writer = TapeMetadataWriter(backend)
    shard_writer = CatalogShardWriter(metadata_writer)
    versioned_manifest_writer = VersionedManifestWriter(metadata_writer)
    manifest_validator = ManifestValidator(metadata_writer, shard_writer)
    path_mapping_service = PathMappingService(repo)
    return ArchiveLifecycleManager(
        repo=repo,
        metadata_writer=metadata_writer,
        shard_writer=shard_writer,
        versioned_manifest_writer=versioned_manifest_writer,
        manifest_validator=manifest_validator,
        path_mapping_service=path_mapping_service,
    )


def _run_lifecycle_for_file(
    file_record: NasFileRecord,
    barcode: str,
    tape_path: str,
    policy_name: str,
    repo,
    backend: MockLTFSBackend,
) -> ArchiveLifecycleResult:
    """Construct lifecycle manager and complete one file archive."""
    manager = _build_archive_lifecycle_manager(repo, backend)
    return manager.complete_file_archive(file_record, barcode, tape_path, policy_name)


def _load_if_needed(library: MockLibraryBackend, barcode: str) -> tuple[int, int | None]:
    drive_id = library.find_drive_by_barcode(barcode)
    if drive_id is not None:
        return drive_id, None
    slot_id = library.find_slot_by_barcode(barcode)
    if slot_id is None:
        raise ValueError(f"Tape {barcode} is not present in simulator inventory")
    for drive in library.inventory().drives:
        if drive.barcode is not None:
            continue
        library.load(slot_id, drive.drive_id)
        return drive.drive_id, slot_id
    raise ValueError("No free simulator tape drive is available")


def _ordered_unique(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
