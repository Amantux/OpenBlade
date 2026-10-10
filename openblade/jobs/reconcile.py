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
