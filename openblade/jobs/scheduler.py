"""Drive scheduler: atomically allocates N drives for parallel tape I/O."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from openblade.domain.errors import DriveBusyError, DriveUnreconciledError, StaleLeaseError
from openblade.domain.models import DriveLease

if TYPE_CHECKING:
    from openblade.catalog.repository import CatalogRepository

logger = logging.getLogger(__name__)


@dataclass
class DriveHandle:
    # The scheduler lock key — the drive this lease reserves. IMMUTABLE for the
    # lease's lifetime: release_drives() frees exactly this key, so mutating it
    # would leak the reserved drive and free one the scheduler never held.
    drive_id: int
    barcode: str
    # The drive the cartridge is physically in, when it differs from the reserved
    # one (e.g. the tape was already loaded elsewhere). Physical tape ops target
    # `physical`; the scheduler lock still tracks `drive_id`.
    physical_drive_id: int | None = None
    # Lease identity + fencing token (docs/decisions/2026-10-09-persistent-drive-leases.md).
    lease_id: str = ""
    fencing_token: int = 0
    _released: bool = field(default=False, init=False, repr=False)

    @property
    def physical(self) -> int:
        """Drive to run physical ops against (falls back to the reserved drive)."""
        return self.physical_drive_id if self.physical_drive_id is not None else self.drive_id


DEFAULT_LEASE_TTL = timedelta(minutes=15)


class LeaseStore(Protocol):
    """Where drive leases live. In memory for unit tests, the catalog in production."""

    poll_interval: float

    def acquire(
        self,
        *,
        job_id: str,
        barcodes: list[str],
        num_drives: int,
        ttl: timedelta,
        exclude: frozenset[int] = frozenset(),
    ) -> list[DriveLease] | None: ...

    def unreconciled_drives(self) -> dict[int, str | None]:
        """Drives awaiting reconciliation (physical state unknown) -> last barcode."""
        ...

    def heartbeat(self, lease_ids: list[str], ttl: timedelta) -> None: ...

    def set_physical_drive(self, lease_id: str, physical_drive_id: int) -> None: ...

    def release(self, lease_ids: list[str]) -> None: ...

    def is_live(self, lease_id: str, fencing_token: int) -> bool: ...

    def live_leases(self) -> list[DriveLease]: ...


class InMemoryLeaseStore:
    """Process-local lease store (the pre-catalog behaviour)."""

    poll_interval = 1.0

    def __init__(self) -> None:
        # Drive -> barcode for drives whose physical state is unknown (tests set this).
        self.unreconciled: dict[int, str | None] = {}
        self._lock = threading.Lock()
        self._leases: dict[str, DriveLease] = {}
        self._last_token = 0

    def acquire(
        self,
        *,
        job_id: str,
        barcodes: list[str],
        num_drives: int,
        ttl: timedelta,
        exclude: frozenset[int] = frozenset(),
    ) -> list[DriveLease] | None:
        with self._lock:
            now = _now()
            busy = {lease.drive_id for lease in self._leases.values() if _live(lease, now)}
            free = [d for d in range(num_drives) if d not in busy and d not in exclude]
            if len(free) < len(barcodes):
                return None
            acquired: list[DriveLease] = []
            for drive_id, barcode in zip(free, barcodes, strict=False):
                self._last_token += 1
                lease = DriveLease(
                    id=str(uuid4()),
                    drive_id=drive_id,
                    job_id=job_id,
                    barcode=barcode,
                    physical_drive_id=None,
                    fencing_token=self._last_token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=now + ttl,
                )
                self._leases[lease.id] = lease
                acquired.append(lease)
            return acquired

    def unreconciled_drives(self) -> dict[int, str | None]:
        return dict(self.unreconciled)

    def heartbeat(self, lease_ids: list[str], ttl: timedelta) -> None:
        with self._lock:
            now = _now()
            for lease_id in lease_ids:
                lease = self._leases.get(lease_id)
                if lease is not None and _live(lease, now):
                    self._leases[lease_id] = replace(lease, heartbeat_at=now, expires_at=now + ttl)

    def set_physical_drive(self, lease_id: str, physical_drive_id: int) -> None:
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is not None:
                self._leases[lease_id] = replace(lease, physical_drive_id=physical_drive_id)

    def release(self, lease_ids: list[str]) -> None:
        with self._lock:
            now = _now()
            for lease_id in lease_ids:
                lease = self._leases.get(lease_id)
                if lease is not None and lease.released_at is None:
                    self._leases[lease_id] = replace(lease, released_at=now)

    def is_live(self, lease_id: str, fencing_token: int) -> bool:
        with self._lock:
            lease = self._leases.get(lease_id)
            return (
                lease is not None and lease.fencing_token == fencing_token and _live(lease, _now())
            )

    def live_leases(self) -> list[DriveLease]:
        with self._lock:
            now = _now()
            return [lease for lease in self._leases.values() if _live(lease, now)]


class CatalogLeaseStore:
    """Lease store shared by every process using the same catalog."""

    poll_interval = 0.25

    def __init__(self, repository: CatalogRepository) -> None:
        self._repo = repository

    def acquire(
        self,
        *,
        job_id: str,
        barcodes: list[str],
        num_drives: int,
        ttl: timedelta,
        exclude: frozenset[int] = frozenset(),
    ) -> list[DriveLease] | None:
        leases = self._repo.acquire_drive_leases(
            job_id=job_id, barcodes=barcodes, num_drives=num_drives, ttl=ttl
        )
        if leases is not None and any(lease.drive_id in exclude for lease in leases):
            # The repository cannot yet skip drives itself: never hand out an
            # unreconciled drive -- give the leases back and report "not now".
            self._repo.release_leases([lease.id for lease in leases])
            return None
        return leases

    def unreconciled_drives(self) -> dict[int, str | None]:
        from openblade.jobs.reconcile import drives_pending_reconciliation  # local: no cycle

        return {
            drive_id: item.barcode
            for drive_id, item in drives_pending_reconciliation(self._repo).items()
        }

    def heartbeat(self, lease_ids: list[str], ttl: timedelta) -> None:
        self._repo.heartbeat_leases(lease_ids, ttl)

    def set_physical_drive(self, lease_id: str, physical_drive_id: int) -> None:
        self._repo.set_lease_physical_drive(lease_id, physical_drive_id)

    def release(self, lease_ids: list[str]) -> None:
        self._repo.release_leases(lease_ids)

    def is_live(self, lease_id: str, fencing_token: int) -> bool:
        return self._repo.lease_is_live(lease_id, fencing_token)

    def live_leases(self) -> list[DriveLease]:
        return self._repo.live_leases()


class DriveScheduler:
    """
    Atomically allocates 1..N drives for parallel tape operations.

    Rules:
    - A drive can be held by at most one job at a time (across every scheduler
      sharing the same lease store).
    - acquire_drives() waits (with timeout) until all requested drives are free.
    - release_drives() releases the leases and wakes local waiters.
    - verify() raises StaleLeaseError once a lease is no longer live (fencing).
    """

    def __init__(
        self,
        num_drives: int,
        *,
        store: LeaseStore | None = None,
        job_id: str = "local",
        ttl: timedelta = DEFAULT_LEASE_TTL,
    ) -> None:
        self._num_drives = num_drives
        self._store: LeaseStore = store if store is not None else InMemoryLeaseStore()
        self._job_id = job_id
        self._ttl = ttl
        self._lock = threading.Condition(threading.Lock())
        # Leases are kept alive by a daemon thread for as long as any handle is
        # held, so a single long tape write (real LTO: minutes to hours per file)
        # cannot outlive its own TTL and be fenced out after the data is down.
        self._held: list[DriveHandle] = []
        self._stop_heartbeat = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @property
    def num_drives(self) -> int:
        return self._num_drives

    def available_count(self) -> int:
        """Number of currently free drives."""
        held = {lease.drive_id for lease in self._store.live_leases()}
        return sum(1 for drive_id in range(self._num_drives) if drive_id not in held)

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
                # A drive whose physical state is unknown (failed unmount/unload)
                # may still hold a mounted tape: never a candidate until reconciled.
                unreconciled = self._store.unreconciled_drives()
                excluded = frozenset(d for d in unreconciled if 0 <= d < self._num_drives)
                if self._num_drives - len(excluded) < len(barcodes):
                    named = ", ".join(
                        f"drive {d} (barcode {unreconciled[d] or 'unknown'})"
                        for d in sorted(excluded)
                    )
                    raise DriveUnreconciledError(
                        f"Not enough reconciled drives for {len(barcodes)} tape(s): {named} "
                        "awaiting reconciliation after a failed unmount/unload"
                    )
                leases = self._store.acquire(
                    job_id=self._job_id,
                    barcodes=barcodes,
                    num_drives=self._num_drives,
                    ttl=self._ttl,
                    exclude=excluded,
                )
                if leases is not None:
                    break
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise DriveBusyError(f"Timed out waiting for {len(barcodes)} free drives")
                self._lock.wait(timeout=min(remaining, self._store.poll_interval))

        handles: list[DriveHandle] = []
        for lease in leases:
            handles.append(
                DriveHandle(
                    drive_id=lease.drive_id,
                    barcode=lease.barcode,
                    lease_id=lease.id,
                    fencing_token=lease.fencing_token,
                )
            )
            logger.info(
                "Allocated drive %d for barcode %s (token %d)",
                lease.drive_id,
                lease.barcode,
                lease.fencing_token,
            )
        with self._lock:
            self._held.extend(handles)
            self._ensure_heartbeat_thread()
        return handles

    def _ensure_heartbeat_thread(self) -> None:
        # Caller holds self._lock.
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="drive-lease-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        interval = max(self._ttl.total_seconds() / 3.0, 0.05)
        while not self._stop_heartbeat.wait(interval):
            with self._lock:
                handles = [handle for handle in self._held if not handle._released]
            if not handles:
                return
            try:
                self.heartbeat(handles)
            except Exception:  # noqa: BLE001 - a missed beat is logged; the next verify() fences
                logger.warning("drive lease heartbeat failed", exc_info=True)

    def release_drives(self, handles: list[DriveHandle]) -> None:
        """Release drives and notify waiting jobs."""
        pending = [handle for handle in handles if not handle._released]
        self._store.release([handle.lease_id for handle in pending])
        with self._lock:
            for handle in pending:
                handle._released = True
                logger.info("Released drive %d (was %s)", handle.drive_id, handle.barcode)
            self._held = [handle for handle in self._held if not handle._released]
            if not self._held:
                self._stop_heartbeat.set()
            self._lock.notify_all()

    def verify(self, handle: DriveHandle) -> None:
        """Raise StaleLeaseError unless the handle's lease is still live with its token."""
        if handle._released or not self._store.is_live(handle.lease_id, handle.fencing_token):
            raise StaleLeaseError(
                f"Lease on drive {handle.drive_id} for {handle.barcode} is no longer live"
            )

    def heartbeat(self, handles: list[DriveHandle]) -> None:
        """Extend the leases behind these handles by the scheduler's ttl."""
        self._store.heartbeat([handle.lease_id for handle in handles], self._ttl)

    def record_physical_drives(self, handles: list[DriveHandle]) -> None:
        """Persist where each cartridge actually ended up (when it differs from the
        reserved drive) so restart recovery reconciles the right drive."""
        for handle in handles:
            if handle.physical_drive_id is not None:
                self._store.set_physical_drive(handle.lease_id, handle.physical_drive_id)

    def status(self) -> dict[int, str | None]:
        """Return copy of drive allocation status."""
        status: dict[int, str | None] = {i: None for i in range(self._num_drives)}
        for lease in self._store.live_leases():
            if lease.drive_id in status:
                status[lease.drive_id] = lease.barcode
        return status


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _live(lease: DriveLease, now: datetime) -> bool:
    return lease.released_at is None and lease.expires_at > now


def _monotonic() -> float:
    import time

    return time.monotonic()
