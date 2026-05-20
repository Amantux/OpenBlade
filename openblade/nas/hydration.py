"""NAS hydration executor for restore jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from openblade.nas.restore_planner import RestorePlan
from openblade.nas.service import NasService
from openblade.nas.types import NasFileRecord, NasFileState, NasRestoreJob, RestoreJobStatus
from openblade.simulator.ltfs_volume import MockLTFSBackend


class _HydrationControlSignal(RuntimeError):
    pass


@dataclass
class HydrationJob:
    job_id: str
    restore_job: NasRestoreJob
    plan: RestorePlan
    status: RestoreJobStatus
    files_restored: int = 0
    files_failed: int = 0
    bytes_restored: int = 0
    errors: list[str] = field(default_factory=list)
    partial_success: bool = False
    _cancelled: bool = False


class HydrationExecutor:
    """Execute NAS restore jobs against the simulator without real filesystem I/O."""

    def __init__(self, service: NasService, ltfs: MockLTFSBackend) -> None:
        self.service = service
        self.ltfs = ltfs

    def run(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        self._require_status(job, {RestoreJobStatus.QUEUED}, "run")
        self._persist_job(
            job.model_copy(
                update={
                    "status": RestoreJobStatus.RUNNING,
                    "bytes_restored": 0,
                    "files_restored": 0,
                    "files_failed": 0,
                    "partial_success": False,
                    "error_message": None,
                }
            )
        )
        return self._execute(job_id)

    def cancel(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        self._require_status(
            job,
            {RestoreJobStatus.QUEUED, RestoreJobStatus.RUNNING, RestoreJobStatus.PAUSED},
            "cancel",
        )
        self._update_status(
            job_id,
            RestoreJobStatus.CANCELLED,
            bytes_restored=job.bytes_restored,
            files_restored=job.files_restored,
            files_failed=job.files_failed,
            partial_success=job.partial_success,
            error_message=job.error_message,
        )
        cancelled = self._require_job(job_id)
        cancelled = self._persist_job(cancelled.model_copy(update={"partial_success": job.partial_success}))
        return cancelled

    def pause(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        self._require_status(job, {RestoreJobStatus.RUNNING}, "pause")
        self._update_status(
            job_id,
            RestoreJobStatus.PAUSED,
            bytes_restored=job.bytes_restored,
            files_restored=job.files_restored,
            files_failed=job.files_failed,
            partial_success=job.partial_success,
            error_message=job.error_message,
        )
        return self._require_job(job_id)

    def resume(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        self._require_status(job, {RestoreJobStatus.PAUSED}, "resume")
        self._persist_job(job.model_copy(update={"status": RestoreJobStatus.RUNNING, "error_message": None}))
        return self._execute(job_id)

    def retry(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        self._require_status(job, {RestoreJobStatus.FAILED}, "retry")
        self._persist_job(
            job.model_copy(
                update={
                    "status": RestoreJobStatus.RUNNING,
                    "bytes_restored": 0,
                    "files_restored": 0,
                    "files_failed": 0,
                    "partial_success": False,
                    "error_message": None,
                }
            )
        )
        return self._execute(job_id)

    def _execute(self, job_id: str) -> NasRestoreJob:
        job = self._require_job(job_id)
        plan = self._plan_from_job(job)
        hydration_job = HydrationJob(
            job_id=job.id,
            restore_job=job,
            plan=plan,
            status=RestoreJobStatus.RUNNING,
            files_restored=job.files_restored,
            files_failed=job.files_failed,
            bytes_restored=job.bytes_restored,
            partial_success=job.partial_success,
        )
        records = self._records_for_job(job)
        records_by_path = {self._normalize_path(record.relative_path): record for record in records}
        groups = plan.parallel_restore_groups or [[barcode] for barcode in plan.tape_load_order]

        try:
            for group in groups:
                self._check_control_state(hydration_job, None)
                for barcode in group:
                    for logical_path in plan.batches_by_tape.get(barcode, []):
                        record = records_by_path.get(self._normalize_path(logical_path))
                        if record is None or record.status is NasFileState.ONLINE_CACHED:
                            continue
                        self._hydrate_record(hydration_job, record)
        except _HydrationControlSignal:
            return self._require_job(job_id)

        final_status = RestoreJobStatus.COMPLETED
        if hydration_job.files_failed > 0 and hydration_job.files_restored > 0:
            hydration_job.partial_success = True
        elif hydration_job.files_restored == 0:
            final_status = RestoreJobStatus.FAILED

        error_message = "; ".join(hydration_job.errors) if hydration_job.errors else None
        if hydration_job.files_restored == 0 and error_message is None:
            error_message = "No files restored"

        self._finalize_job(hydration_job, final_status, error_message)
        return self._require_job(job_id)

    def _hydrate_record(self, hydration_job: HydrationJob, record: NasFileRecord) -> None:
        original_status = record.status
        self._set_file_status(record, NasFileState.HYDRATING)
        try:
            self._check_control_state(hydration_job, record)
            simulated_content = self._simulate_content(record)
            self._check_control_state(hydration_job, record)
            destination = str(PurePosixPath(hydration_job.restore_job.destination) / record.relative_path)
            restored = self._persist_file_record(
                record.model_copy(
                    update={
                        "status": NasFileState.ONLINE_CACHED,
                        "cache_path": destination,
                    }
                )
            )
            hydration_job.files_restored += 1
            hydration_job.bytes_restored += len(simulated_content)
            self._update_status(
                hydration_job.job_id,
                RestoreJobStatus.RUNNING,
                bytes_restored=hydration_job.bytes_restored,
                files_restored=hydration_job.files_restored,
                files_failed=hydration_job.files_failed,
                partial_success=False,
            )
            hydration_job.restore_job = self._require_job(hydration_job.job_id)
            hydration_job.status = RestoreJobStatus.RUNNING
            record.status = restored.status
            record.cache_path = restored.cache_path
        except _HydrationControlSignal:
            self._set_file_status(record, original_status)
            raise
        except Exception as exc:
            self._persist_file_record(record.model_copy(update={"status": NasFileState.FAILED}))
            hydration_job.files_failed += 1
            hydration_job.errors.append(f"{record.relative_path}: {exc}")
            self._update_status(
                hydration_job.job_id,
                RestoreJobStatus.RUNNING,
                bytes_restored=hydration_job.bytes_restored,
                files_restored=hydration_job.files_restored,
                files_failed=hydration_job.files_failed,
                partial_success=False,
                error_message="; ".join(hydration_job.errors),
            )
            hydration_job.restore_job = self._require_job(hydration_job.job_id)

    def _check_control_state(
        self,
        hydration_job: HydrationJob,
        record: NasFileRecord | None,
    ) -> None:
        current_job = self._require_job(hydration_job.job_id)
        hydration_job.restore_job = current_job
        hydration_job.status = current_job.status
        if current_job.status is RestoreJobStatus.CANCELLED:
            hydration_job._cancelled = True
            raise _HydrationControlSignal("cancelled")
        if current_job.status is RestoreJobStatus.PAUSED:
            raise _HydrationControlSignal("paused")
        if record is not None and current_job.status is not RestoreJobStatus.RUNNING:
            raise _HydrationControlSignal("stopped")

    def _finalize_job(
        self,
        hydration_job: HydrationJob,
        status: RestoreJobStatus,
        error_message: str | None,
    ) -> None:
        self._update_status(
            hydration_job.job_id,
            status,
            bytes_restored=hydration_job.bytes_restored,
            files_restored=hydration_job.files_restored,
            files_failed=hydration_job.files_failed,
            partial_success=hydration_job.partial_success,
            error_message=error_message,
        )

    def _plan_from_job(self, job: NasRestoreJob) -> RestorePlan:
        ordered_groups = [job.parallel_restore_groups[key] for key in sorted(job.parallel_restore_groups)]
        requested_paths = [self._normalize_path(path) for path in job.paths]
        batches_by_tape: dict[str, list[str]] = {barcode: [] for barcode in job.required_tapes}
        for record in self._records_for_job(job):
            logical_path = self._normalize_path(record.relative_path)
            if requested_paths and logical_path not in set(requested_paths):
                continue
            if record.tape_barcode is None:
                continue
            batches_by_tape.setdefault(record.tape_barcode, []).append(logical_path)
        return RestorePlan(
            job_id=job.id,
            pool_id=job.pool_id,
            requested_paths=requested_paths,
            destination=job.destination,
            priority=job.priority,
            allow_parallel=job.allow_parallel,
            max_drives=job.max_drives,
            required_tapes=list(job.required_tapes),
            missing_tapes=list(job.missing_tapes),
            exported_tapes=list(job.exported_tapes),
            tape_load_order=list(job.tape_load_order),
            batches_by_tape=batches_by_tape,
            parallel_restore_groups=ordered_groups,
            estimated_tape_swaps=max(len(ordered_groups) - 1, 0),
            estimated_bytes=job.estimated_bytes,
            unavailable_files=list(job.unavailable_files),
            warnings=list(job.warnings),
            is_safe_to_enqueue=not job.unavailable_files and not job.missing_tapes and not job.exported_tapes,
        )

    def _records_for_job(self, job: NasRestoreJob) -> list[NasFileRecord]:
        if job.pool_id is None:
            return []
        requested_paths = {self._normalize_path(path) for path in job.paths}
        records = self.service.list_pool_file_records(job.pool_id)
        if not requested_paths:
            return records
        return [
            record
            for record in records
            if self._normalize_path(record.relative_path) in requested_paths
        ]

    def _simulate_content(self, record: NasFileRecord) -> bytes:
        barcode = record.tape_barcode or "<unknown>"
        return f"HYDRATED:{record.relative_path}:{barcode}".encode()

    def _set_file_status(self, record: NasFileRecord, status: NasFileState) -> NasFileRecord:
        saved = self._persist_file_record(record.model_copy(update={"status": status}))
        record.status = saved.status
        return saved

    def _persist_file_record(self, record: NasFileRecord) -> NasFileRecord:
        return self.service.upsert_file_record(record)

    def _persist_job(self, job: NasRestoreJob) -> NasRestoreJob:
        return self.service.upsert_restore_job(job)

    def _update_status(
        self,
        job_id: str,
        status: RestoreJobStatus,
        *,
        bytes_restored: int | None = None,
        files_restored: int | None = None,
        files_failed: int | None = None,
        partial_success: bool | None = None,
        error_message: str | None = None,
    ) -> None:
        self.service.update_restore_job_status(
            job_id,
            status.value,
            bytes_restored=bytes_restored,
            files_restored=files_restored,
            files_failed=files_failed,
            partial_success=partial_success,
            error_message=error_message,
        )

    def _require_job(self, job_id: str) -> NasRestoreJob:
        job = self.service.get_restore_job(job_id)
        if job is None:
            raise KeyError(f"Restore job {job_id} not found")
        return job

    @staticmethod
    def _require_status(job: NasRestoreJob, allowed: set[RestoreJobStatus], action: str) -> None:
        if job.status not in allowed:
            allowed_names = ", ".join(sorted(status.value for status in allowed))
            raise ValueError(f"Cannot {action} restore job from {job.status.value}; allowed: {allowed_names}")

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = str(PurePosixPath("/" + str(path or "").lstrip("/"))).lstrip("/")
        return "" if normalized == "." else normalized
