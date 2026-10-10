"""Drives whose physical state is unknown after a failed unmount/unload.

``record_physical_state_unknown`` journals ``physical_state_unknown`` when an
unmount/unload fails: the drive may still hold a (possibly LTFS-mounted) tape.
Until a later ``drive_reconciled`` entry names the same drive, no job may
acquire or load into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from openblade.catalog.repository import CatalogRepository
from openblade.domain.backends import LibraryBackend
from openblade.domain.errors import DriveUnreconciledError
from openblade.domain.models import MountState
from openblade.jobs.inventory import InventoryService

PHYSICAL_STATE_UNKNOWN = "physical_state_unknown"
DRIVE_RECONCILED = "drive_reconciled"


@dataclass(frozen=True)
class PendingReconciliation:
    drive_id: int
    barcode: str | None
    op: str
    job_id: str
    at: datetime


def _drive_of(detail: dict[str, object]) -> int | None:
    drive = detail.get("drive")
    if drive is None or isinstance(drive, bool):
        return None
    try:
        return int(str(drive))
    except ValueError:
        return None


def drives_pending_reconciliation(catalog: CatalogRepository) -> dict[int, PendingReconciliation]:
    """Drives whose latest ``physical_state_unknown`` has no later ``drive_reconciled``."""
    events = catalog.journal_events(PHYSICAL_STATE_UNKNOWN) + catalog.journal_events(
        DRIVE_RECONCILED
    )
    events.sort(key=lambda entry: (entry.at, entry.id))
    pending: dict[int, PendingReconciliation] = {}
    for entry in events:
        detail = entry.detail
        drive = _drive_of(detail)
        if drive is None:
            continue
        if entry.event == DRIVE_RECONCILED:
            pending.pop(drive, None)
            continue
        barcode = detail.get("barcode")
        pending[drive] = PendingReconciliation(
            drive_id=drive,
            barcode=None if barcode is None else str(barcode),
            op=str(detail.get("op", "")),
            job_id=entry.job_id,
            at=entry.at,
        )
    return pending


def ensure_drive_reconciled(catalog: CatalogRepository, drive_id: int) -> None:
    """Refuse to use ``drive_id`` while its physical state is unknown."""
    item = drives_pending_reconciliation(catalog).get(drive_id)
    if item is not None:
        raise DriveUnreconciledError(
            f"Drive {drive_id} (barcode {item.barcode or 'unknown'}) awaits reconciliation "
            f"after a failed {item.op or 'operation'}; reconcile it before reuse"
        )


class UnknownDriveError(LookupError):
    """The library reports no drive with this id."""


def reconcile_drive(
    catalog: CatalogRepository,
    library: LibraryBackend,
    drive_id: int,
    *,
    job_id: str = "recovery",
) -> PendingReconciliation:
    """Mark ``drive_id`` reconciled against a fresh inventory snapshot.

    Refuses while the drive still reports an LTFS mount: the operator must
    unmount first. Returns the ``drive_reconciled`` record (``op`` is
    ``drive_reconciled``; ``barcode`` is the barcode observed in the drive).
    """
    snapshot = InventoryService(library).snapshot()  # SAFETY_003: via service
    drive = next((d for d in snapshot.drives if d.drive_id == drive_id), None)
    if drive is None:
        raise UnknownDriveError(f"Drive {drive_id} not found")
    observed = None if drive.barcode is None else str(drive.barcode)
    if drive.mount_state != MountState.UNMOUNTED:
        raise DriveUnreconciledError(
            f"Drive {drive_id} (barcode {observed or 'unknown'}) is still "
            f"{drive.mount_state.value}; unmount it before reconciling"
        )
    entry = catalog.journal(
        job_id, DRIVE_RECONCILED, {"drive": drive_id, "observed_barcode": observed}
    )
    return PendingReconciliation(
        drive_id=drive_id, barcode=observed, op=DRIVE_RECONCILED, job_id=job_id, at=entry.at
    )
