"""Job queue persisted in the catalog, with resource ownership tracking."""

import json
import logging
import threading
import time
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
from openblade.domain.models import DriveLease, Job, JobState, JobType
from openblade.jobs.scheduler import DEFAULT_LEASE_TTL, LeaseStore

ResultT = TypeVar("ResultT")

logger = logging.getLogger(__name__)

# Changer ownership is a lease on this reserved pseudo-drive id. The lease store
# only allocates ids in range(num_drives) (no negatives), and claiming a slot
# probes every free id below it, so the sentinel is kept small: it must exceed
# any real drive count (a Scalar i3 tops out well below it) but stay cheap.
CHANGER_DRIVE_ID = 64
_CHANGER_KIND = "CHANGER"
_DRIVE_KIND = "QUEUE-CLAIM"
_PROBE_BARCODE = "QUEUE-PROBE"
_CLAIM_ATTEMPTS = 50
_CLAIM_RETRY_SECONDS = 0.01


def _is_transient(lease: DriveLease) -> bool:
    """True for another claimer's in-flight lease, which never denotes ownership.

    Claim barcodes name their target (``KIND:slot``). When a claimer's snapshot
    is stale the store can hand its claim barcode to a lower id; that lease, like
    every probe, is released immediately and must not read as an owner.
    """
    if lease.barcode == _PROBE_BARCODE:
        return True
    kind, sep, _ = lease.barcode.partition(":")
    return (
        bool(sep)
        and kind in (_DRIVE_KIND, _CHANGER_KIND)
        and lease.barcode != f"{kind}:{lease.drive_id}"
    )


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

    def _claim_slot(
        self, slot: int, job_id: str, kind: str, busy: Callable[[str], Exception]
    ) -> None:
        for _attempt in range(_CLAIM_ATTEMPTS):
            live = self._lease_store.live_leases()
            for lease in live:
                if lease.drive_id == slot and not _is_transient(lease):
                    if lease.job_id == job_id:
                        return
                    raise busy(lease.job_id)
            # The store allocates the lowest free ids in range(num_drives); it
            # cannot target one id. So request every free id up to and including
            # ``slot`` in one all-or-nothing transaction, keep ``slot``, and
            # release the probes. A concurrent claimer's probes make this miss;
            # re-check and retry until ``slot`` has a real owner or is ours.
            taken = {lease.drive_id for lease in live}
            probes = sum(1 for d in range(slot) if d not in taken)
            leases = self._lease_store.acquire(
                job_id=job_id,
                barcodes=[_PROBE_BARCODE] * probes + [f"{kind}:{slot}"],
                num_drives=slot + 1,
                ttl=self._ttl,
            )
            if leases is not None:
                extra = [lease.id for lease in leases if lease.drive_id != slot]
                if extra:
                    self._lease_store.release(extra)
                if len(extra) < len(leases):
                    return
            time.sleep(_CLAIM_RETRY_SECONDS)
        raise busy("another job (claim contended)")

    def _release_slot(self, slot: int, job_id: str) -> None:
        mine = [
            lease.id
            for lease in self._lease_store.live_leases()
            if lease.drive_id == slot and lease.job_id == job_id
        ]
        if mine:
            self._lease_store.release(mine)

    def claim_drive(self, drive_id: int, job_id: str) -> None:
        if drive_id < 0 or drive_id >= CHANGER_DRIVE_ID:
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
