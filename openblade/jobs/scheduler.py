"""Drive scheduler: atomically allocates N drives for parallel tape I/O."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from openblade.domain.errors import DriveBusyError

logger = logging.getLogger(__name__)


@dataclass
class DriveHandle:
    drive_id: int
    barcode: str
    _released: bool = field(default=False, init=False, repr=False)


class DriveScheduler:
    """
    Atomically allocates 1..N drives for parallel tape operations.

    Rules:
    - A drive can be held by at most one job at a time.
    - acquire_drives() waits (with timeout) until all requested drives are free.
    - release_drives() marks drives as free and notifies waiting jobs.
    """

    def __init__(self, num_drives: int) -> None:
        self._num_drives = num_drives
        self._lock = threading.Condition(threading.Lock())
        self._held: dict[int, str | None] = {i: None for i in range(num_drives)}

    @property
    def num_drives(self) -> int:
        return self._num_drives

    def available_count(self) -> int:
        """Number of currently free drives."""
        with self._lock:
            return sum(1 for value in self._held.values() if value is None)

    def acquire_drives(
        self,
        barcodes: list[str],
        timeout: float = 300.0,
    ) -> list[DriveHandle]:
        """
        Atomically acquire one drive per barcode.

        - If len(barcodes) > num_drives, raises DriveBusyError immediately.
        - If barcodes are not available within timeout, raises DriveBusyError.
        - If a barcode is already locked in the same request, raises ValueError.
        - Returns DriveHandle list in the same order as barcodes.
        """
        if len(barcodes) > self._num_drives:
            raise DriveBusyError(
                f"Requested {len(barcodes)} drives but only {self._num_drives} exist"
            )
        if len(barcodes) != len(set(barcodes)):
            raise ValueError("Duplicate barcodes in acquire_drives request")

        with self._lock:
            deadline = _monotonic() + timeout
            while True:
                free_drives = [drive_id for drive_id, value in self._held.items() if value is None]
                if len(free_drives) >= len(barcodes):
                    break
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise DriveBusyError(f"Timed out waiting for {len(barcodes)} free drives")
                self._lock.wait(timeout=min(remaining, 1.0))

            handles: list[DriveHandle] = []
            free_iter = iter(free_drives)
            for barcode in barcodes:
                drive_id = next(free_iter)
                self._held[drive_id] = barcode
                handles.append(DriveHandle(drive_id=drive_id, barcode=barcode))
                logger.info("Allocated drive %d for barcode %s", drive_id, barcode)

            return handles

    def release_drives(self, handles: list[DriveHandle]) -> None:
        """Release drives and notify waiting jobs."""
        with self._lock:
            for handle in handles:
                if handle._released:
                    continue
                self._held[handle.drive_id] = None
                handle._released = True
                logger.info("Released drive %d (was %s)", handle.drive_id, handle.barcode)
            self._lock.notify_all()

    def status(self) -> dict[int, str | None]:
        """Return copy of drive allocation status."""
        with self._lock:
            return dict(self._held)


def _monotonic() -> float:
    import time

    return time.monotonic()
