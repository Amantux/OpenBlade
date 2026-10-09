"""Startup recovery: reconcile catalog jobs and drive leases after a restart.

Report-only for physical state: this module never moves media.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend

logger = logging.getLogger(__name__)

INTERRUPTED_ERROR = (
    "interrupted by process restart; physical state unknown — reconcile before retry"
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
    """Fail interrupted jobs, release orphaned leases, and report drive mismatches."""
    interrupted: list[str] = []
    for job in catalog.list_jobs(state="running"):
        catalog.update_job_state(job.id, "failed_recoverable", INTERRUPTED_ERROR)
        interrupted.append(job.id)

    orphaned = []
    for lease in catalog.live_leases():
        owner = catalog.get_job(lease.job_id)
        if owner is None or owner.state != "running":
            orphaned.append(lease)
    catalog.release_leases([lease.id for lease in orphaned])

    observed: dict[int, str | None] = {
        drive.drive_id: None if drive.barcode is None else str(drive.barcode)
        for drive in library.inventory().drives
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
