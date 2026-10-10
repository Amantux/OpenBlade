"""Job queue persisted in the catalog, with resource ownership tracking."""

import json
import logging
import threading
from collections.abc import Callable
from datetime import timedelta, timezone
from typing import TypeVar

from openblade.catalog import models as catalog_models
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import (
    ChangerBusyError,
    DriveOccupiedError,
    JobNotFoundError,
    safe_job_error,
)
from openblade.domain.models import Job, JobState, JobType
from openblade.jobs.scheduler import DEFAULT_LEASE_TTL, LeaseStore

ResultT = TypeVar("ResultT")

logger = logging.getLogger(__name__)


def _job_from_row(row: catalog_models.Job) -> Job:
    return Job(
        id=row.id,
        job_type=JobType(row.job_type),
        state=JobState(row.state),
        created_at=row.created_at.replace(tzinfo=timezone.utc),
        updated_at=row.updated_at.replace(tzinfo=timezone.utc),
        error=row.error,
        metadata=json.loads(row.metadata_json or "{}"),
    )


class JobQueue:
    """Jobs live in the catalog ``jobs`` table, so every process sharing it sees them."""

    def __init__(
        self,
        catalog: CatalogRepository,
        lease_store: LeaseStore,
        *,
        ttl: timedelta = DEFAULT_LEASE_TTL,
    ) -> None:
        self._catalog = catalog
        self._lease_store = lease_store
        self._ttl = ttl
        self._lock = threading.RLock()
        self._drive_owners: dict[int, str] = {}
        self._changer_owner: str | None = None

    def create_job(self, job_type: JobType, metadata: dict[str, object]) -> Job:
        with self._lock:
            row = self._catalog.create_job(JobType(job_type).value, dict(metadata))
            return _job_from_row(row)

    def get_job(self, job_id: str) -> Job:
        with self._lock:
            row = self._catalog.get_job(job_id)
            if row is None:
                raise JobNotFoundError(job_id)
            self._catalog.session.refresh(row)
            return _job_from_row(row)

    def update_job(
        self, job_id: str, *, state: JobState | None = None, error: str | None = None
    ) -> Job:
        with self._lock:
            job = self.get_job(job_id)
            self._catalog.update_job_state(job_id, (state or job.state).value, error)
            return self.get_job(job_id)

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
            logger.warning("job %s failed", job.id, exc_info=True)
            self.update_job(job.id, state=JobState.FAILED, error=safe_job_error(exc))
            raise
        completed = self.update_job(job.id, state=JobState.COMPLETED, error=None)
        return completed, result
