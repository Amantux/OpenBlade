"""Startup recovery: reconcile catalog jobs and drive leases after a restart.

Report-only for physical state: this module never moves media.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from openblade.catalog.repository import CatalogRepository, StagedInstance
from openblade.domain.backends import LibraryBackend
from openblade.domain.models import DriveLease
from openblade.jobs.inventory import InventoryService
from openblade.jobs.reconcile import PendingReconciliation, drives_pending_reconciliation
from openblade.jobs.scheduler import DEFAULT_LEASE_TTL

# Synthetic barcodes JobQueue stores on its slot-claim leases (`f"{kind}:{slot}"`,
# kinds _DRIVE_KIND / _CHANGER_KIND in jobs/queue.py). They never name a cartridge.
_QUEUE_CLAIM_PREFIXES = ("QUEUE-CLAIM:", "CHANGER:")


def _is_queue_claim(lease: DriveLease) -> bool:
    return lease.drive_id < 0 or lease.barcode.startswith(_QUEUE_CLAIM_PREFIXES)


logger = logging.getLogger(__name__)

_NON_TERMINAL_JOB_STATES = frozenset({"pending", "running"})
_FAILED_JOB_STATES = frozenset({"failed", "failed_recoverable"})

INTERRUPTED_ERROR = (
    "interrupted: drive lease expired without a heartbeat; physical state unknown — "
    "reconcile before retry"
)


@dataclass(frozen=True)
class DriveMismatch:
    lease_id: str
    job_id: str
    drive_id: int
    expected_barcode: str
    observed_barcode: str | None


@dataclass(frozen=True)
class RecoveryReport:
    interrupted_job_ids: list[str] = field(default_factory=list)
    released_lease_ids: list[str] = field(default_factory=list)
    mismatches: list[DriveMismatch] = field(default_factory=list)
    # Uncommitted (STAGING/VERIFYING) instances per `failed_recoverable` job: what an
    # operator must reconcile on tape before retrying or discarding the job.
    staged_instances: dict[str, list[StagedInstance]] = field(default_factory=dict)
    # `pending` jobs older than one lease TTL with no live lease. Report only.
    stale_pending_job_ids: list[str] = field(default_factory=list)
    # Drives whose physical state is unknown after a failed unmount/unload. Report
    # only: an operator reconciles each via POST /jobs/recovery/reconcile/{drive_id}.
    pending_reconciliation: list[PendingReconciliation] = field(default_factory=list)
    # Live leases of FAILED jobs, deliberately NOT released: a failed job releases
    # its leases in `finally` unless its physical_state_unknown could not be
    # journaled (fail closed). Report only; the lease lapses at its TTL.
    held_failed_lease_ids: list[str] = field(default_factory=list)


def _is_older_than(created_at: datetime, ttl: timedelta, now: datetime) -> bool:
    # Job.created_at is written as naive UTC; tolerate an aware value too.
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(UTC).replace(tzinfo=None)
    return created_at < now - ttl


def recover_after_restart(catalog: CatalogRepository, library: LibraryBackend) -> RecoveryReport:
    """Fail jobs whose leases expired, release orphaned leases, report drive mismatches.

    The catalog is shared by every process (API + CLI), so "state == running" is
    NOT evidence that a job is dead — it may be running in another process. The
    only cross-process evidence is a lease that stopped heartbeating. Jobs that
    are running without a lease are left alone.
    """
    interrupted: list[str] = []
    orphaned = []
    for lease in catalog.expired_leases():
        orphaned.append(lease)
        owner = catalog.get_job(lease.job_id)
        if owner is not None and owner.state == "running" and owner.id not in interrupted:
            catalog.update_job_state(owner.id, "failed_recoverable", INTERRUPTED_ERROR)
            interrupted.append(owner.id)
            catalog.journal(
                owner.id, "recovered", {"lease_id": lease.id, "error": INTERRUPTED_ERROR}
            )
    # Bookkeeping: a live lease whose job already reached a terminal state.
    live = catalog.live_leases()
    held: list[DriveLease] = []
    leased_job_ids = {lease.job_id for lease in live}
    for lease in live:
        owner = catalog.get_job(lease.job_id)
        # A pending or running owner may belong to another live process; its
        # lease is only reclaimed once it expires (handled above).
        if owner is not None and owner.state in _FAILED_JOB_STATES:
            held.append(lease)
        elif owner is None or owner.state not in _NON_TERMINAL_JOB_STATES:
            orphaned.append(lease)
    catalog.release_leases([lease.id for lease in orphaned])

    observed: dict[int, str | None] = {
        drive.drive_id: None if drive.barcode is None else str(drive.barcode)
        for drive in InventoryService(library).snapshot().drives  # SAFETY_003: via service
    }
    mismatches: list[DriveMismatch] = []
    for lease in orphaned:
        if _is_queue_claim(lease):
            continue  # a JobQueue slot claim, not a tape in a drive: nothing to compare
        physical = (
            lease.physical_drive_id if lease.physical_drive_id is not None else lease.drive_id
        )
        seen = observed.get(physical)
        if seen != lease.barcode:
            mismatch = DriveMismatch(lease.id, lease.job_id, physical, lease.barcode, seen)
            logger.warning(
                "recovery: drive %s expected %s, observed %s (lease %s, job %s)",
                physical,
                lease.barcode,
                seen,
                lease.id,
                lease.job_id,
            )
            mismatches.append(mismatch)

    staged = {
        job.id: catalog.list_staged_instances(job.id)
        for job in catalog.list_jobs(state="failed_recoverable")
    }
    # Evidence is the lease set observed before this pass released anything.
    now = datetime.now(UTC).replace(tzinfo=None)
    stale_pending = [
        job.id
        for job in catalog.list_jobs(state="pending")
        if job.id not in leased_job_ids and _is_older_than(job.created_at, DEFAULT_LEASE_TTL, now)
    ]

    report = RecoveryReport(
        interrupted_job_ids=interrupted,
        released_lease_ids=[lease.id for lease in orphaned],
        mismatches=mismatches,
        staged_instances=staged,
        stale_pending_job_ids=stale_pending,
        pending_reconciliation=list(drives_pending_reconciliation(catalog).values()),
        held_failed_lease_ids=[lease.id for lease in held],
    )
    for lease in held:
        logger.error(
            "recovery: keeping lease %s on drive %d (failed job %s); reconcile the drive",
            lease.id,
            lease.drive_id,
            lease.job_id,
        )
    logger.info(
        "recovery: %d job(s) interrupted, %d lease(s) released, %d drive mismatch(es), "
        "%d staged instance(s) awaiting reconcile, %d stale pending job(s), "
        "%d drive(s) awaiting reconciliation",
        len(report.interrupted_job_ids),
        len(report.released_lease_ids),
        len(report.mismatches),
        sum(len(items) for items in staged.values()),
        len(stale_pending),
        len(report.pending_reconciliation),
    )
    return report
