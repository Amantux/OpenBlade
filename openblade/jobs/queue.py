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

# Changer ownership is a lease on this reserved pseudo-drive id. Real drives
# are 0..N-1, so a negative id can never collide with one.
CHANGER_DRIVE_ID = -1
_CHANGER_KIND = "CHANGER"
_DRIVE_KIND = "QUEUE-CLAIM"


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
        # Guards the shared catalog session only; cross-process ownership is the
        # catalog's BEGIN IMMEDIATE inside lease acquisition.
        self._lock = threading.RLock()

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

    def _owner(self, slot: int) -> str | None:
        for lease in self._lease_store.live_leases():
            if lease.drive_id == slot:
                return lease.job_id
        return None

    def _claim_slot(
        self, slot: int, job_id: str, kind: str, busy: Callable[[str], Exception]
    ) -> None:
        owner = self._owner(slot)
        if owner == job_id:
            return
        if owner is None:
            lease = self._lease_store.acquire_drive_lease_at(
                job_id=job_id, drive_id=slot, barcode=f"{kind}:{slot}", ttl=self._ttl
            )
            if lease is not None:
                return
            owner = self._owner(slot)
            if owner == job_id:
                return
        raise busy(owner or "another job")

    def _release_slot(self, slot: int, job_id: str) -> None:
        mine = [
            lease.id
            for lease in self._lease_store.live_leases()
            if lease.drive_id == slot and lease.job_id == job_id
        ]
        if mine:
            self._lease_store.release(mine)

    def claim_drive(self, drive_id: int, job_id: str) -> None:
        if drive_id < 0:
            raise ValueError(f"drive id {drive_id} out of range")
        self._claim_slot(
            drive_id,
            job_id,
            _DRIVE_KIND,
            lambda owner: DriveOccupiedError(f"Drive {drive_id} is already owned by job {owner}"),
        )

    def release_drive(self, drive_id: int, job_id: str) -> None:
        self._release_slot(drive_id, job_id)

    def claim_changer(self, job_id: str) -> None:
        self._claim_slot(
            CHANGER_DRIVE_ID,
            job_id,
            _CHANGER_KIND,
            lambda owner: ChangerBusyError(f"Changer owned by job {owner}"),
        )

    def release_changer(self, job_id: str) -> None:
        self._release_slot(CHANGER_DRIVE_ID, job_id)

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
