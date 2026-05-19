"""In-process job queue with resource ownership tracking."""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import TypeVar

from openblade.domain.errors import ChangerBusyError, DriveOccupiedError, JobNotFoundError
from openblade.domain.models import Job, JobState, JobType

ResultT = TypeVar("ResultT")


class JobQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._drive_owners: dict[int, str] = {}
        self._changer_owner: str | None = None

    def create_job(self, job_type: JobType, metadata: dict[str, object]) -> Job:
        job = Job(job_type=job_type, metadata=dict(metadata))
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> Job:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFoundError(job_id) from exc

    def _set_job(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def update_job(
        self, job_id: str, *, state: JobState | None = None, error: str | None = None
    ) -> Job:
        job = self.get_job(job_id)
        updated = replace(
            job,
            state=state or job.state,
            error=error,
            updated_at=datetime.now(timezone.utc),
        )
        self._set_job(updated)
        return updated

    def claim_drive(self, drive_id: int, job_id: str) -> None:
        with self._lock:
            owner = self._drive_owners.get(drive_id)
            if owner is not None and owner != job_id:
                raise DriveOccupiedError(f"Drive {drive_id} is already owned by job {owner}")
            self._drive_owners[drive_id] = job_id

    def release_drive(self, drive_id: int, job_id: str) -> None:
        with self._lock:
            if self._drive_owners.get(drive_id) == job_id:
                del self._drive_owners[drive_id]

    def claim_changer(self, job_id: str) -> None:
        with self._lock:
            if self._changer_owner is not None and self._changer_owner != job_id:
                raise ChangerBusyError(f"Changer owned by job {self._changer_owner}")
            self._changer_owner = job_id

    def release_changer(self, job_id: str) -> None:
        with self._lock:
            if self._changer_owner == job_id:
                self._changer_owner = None

    def run_job(self, job: Job, func: Callable[[], ResultT]) -> tuple[Job, ResultT]:
        self.update_job(job.id, state=JobState.RUNNING)
        try:
            result = func()
        except Exception as exc:
            self.update_job(job.id, state=JobState.FAILED, error=str(exc))
            raise
        completed = self.update_job(job.id, state=JobState.COMPLETED, error=None)
        return completed, result
