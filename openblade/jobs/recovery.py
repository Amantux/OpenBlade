"""Startup recovery: reconcile catalog jobs and drive leases after a restart.

Report-only for physical state: this module never moves media.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend
from openblade.jobs.inventory import InventoryService

logger = logging.getLogger(__name__)

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
    # Bookkeeping: a live lease whose job already reached a terminal state.
    for lease in catalog.live_leases():
        owner = catalog.get_job(lease.job_id)
        if owner is None or owner.state != "running":
            orphaned.append(lease)
    catalog.release_leases([lease.id for lease in orphaned])

    observed: dict[int, str | None] = {
        drive.drive_id: None if drive.barcode is None else str(drive.barcode)
        for drive in InventoryService(library).snapshot().drives  # SAFETY_003: via service
    }
    mismatches: list[DriveMismatch] = []
    for lease in orphaned:
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

    report = RecoveryReport(interrupted, [lease.id for lease in orphaned], mismatches)
    logger.info(
        "recovery: %d job(s) interrupted, %d lease(s) released, %d drive mismatch(es)",
        len(report.interrupted_job_ids),
        len(report.released_lease_ids),
        len(report.mismatches),
    )
    return report
