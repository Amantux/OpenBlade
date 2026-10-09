"""Hydration queue: request that an offline file be brought online."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from openblade.fuse.cache import CacheChecksumError, HydrationCache


@dataclass
class HydrationRequest:
    catalog_path: str
    priority: int = 0


class HydrationQueue:
    def __init__(self) -> None:
        self._queue: list[HydrationRequest] = []

    def enqueue(self, request: HydrationRequest) -> None:
        self._queue.append(request)

    def pending(self) -> list[HydrationRequest]:
        return list(self._queue)

    def clear(self) -> None:
        self._queue.clear()


# -- hydration data plane ----------------------------------------------------
#
# Layering: the Hydrator owns tickets, batching and the cache hand-off; a
# RestoreEngine owns *how* bytes come back from tape. The production engine goes
# through RestoreService (the job machinery). Nothing here touches a tape
# backend, formats media, or moves cartridges directly.

DEFAULT_HYDRATE_TIMEOUT_S = 300.0
DEFAULT_BATCH_WINDOW_MS = 250


def hydrate_timeout_from_env() -> float:
    raw = os.environ.get("OPENBLADE_FUSE_HYDRATE_TIMEOUT", "").strip()
    return float(raw) if raw else DEFAULT_HYDRATE_TIMEOUT_S


def batch_window_from_env() -> float:
    raw = os.environ.get("OPENBLADE_FUSE_BATCH_WINDOW_MS", "").strip()
    return (int(raw) if raw else DEFAULT_BATCH_WINDOW_MS) / 1000.0


class HydrationState(str, Enum):
    OFFLINE = "offline"
    HYDRATING = "hydrating"
    ONLINE = "online"


class HydrationFailedError(RuntimeError):
    """A restore for hydration failed; the message is curated, never raw."""


class HydrationTimeoutError(TimeoutError):
    """The file did not come online within the wait budget."""


@dataclass
class HydrationTicket:
    catalog_path: str
    checksum: str
    tape_key: str
    job_id: str | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class RestoreEngine(Protocol):
    def restore_batch(self, tape_key: str, catalog_paths: list[str]) -> dict[str, Path]:
        """Restore every path (all on one tape) and return staged file paths."""

    def find_active_job(self, catalog_path: str) -> str | None:
        """Return the id of a pending/running restore job for the path, if any."""

    def resume(self, job_id: str) -> Path:
        """Wait for an existing job to finish and return its staged file."""


class JobRestoreEngine:
    """Production engine: one restore job per file via ``RestoreService``.

    Files in one batch share a tape and are restored back to back, so the
    library loads that cartridge once (scheduler/lease policy is the jobs
    layer's concern, not ours).
    """

    def __init__(self, restore_service: Any, catalog: Any, staging_root: Path) -> None:
        self.restore_service = restore_service
        self.catalog = catalog
        self.staging_root = staging_root

    def restore_batch(self, tape_key: str, catalog_paths: list[str]) -> dict[str, Path]:
        del tape_key
        staged: dict[str, Path] = {}
        for path in catalog_paths:
            record = self.catalog.get_file_record(path)
            if record is None:
                raise HydrationFailedError(f"{path} is not in the catalog")
            dest = self.staging_root / record.id
            dest.parent.mkdir(parents=True, exist_ok=True)
            job = self.restore_service.enqueue(path, dest)
            if job.state != "completed":
                raise HydrationFailedError(f"restore job {job.id} for {path} ended {job.state}")
            staged[path] = dest
        return staged

    def find_active_job(self, catalog_path: str) -> str | None:
        for job in self.catalog.list_jobs():
            if job.job_type != "restore" or job.state not in {"pending", "running"}:
                continue
            if json.loads(job.metadata_json or "{}").get("catalog_path") == catalog_path:
                return str(job.id)
        return None

    def resume(self, job_id: str) -> Path:
        while True:
            job = self.catalog.get_job(job_id)
            if job is None:
                raise HydrationFailedError(f"restore job {job_id} disappeared")
            if job.state == "completed":
                return Path(json.loads(job.metadata_json)["dest_path"])
            if job.state not in {"pending", "running"}:
                raise HydrationFailedError(f"restore job {job_id} ended {job.state}")
            time.sleep(0.5)


class Hydrator:
    def __init__(
        self,
        catalog: Any,
        cache: HydrationCache,
        engine: RestoreEngine,
        *,
        batch_window_s: float | None = None,
    ) -> None:
        self.catalog = catalog
        self.cache = cache
        self.engine = engine
        self.batch_window_s = batch_window_from_env() if batch_window_s is None else batch_window_s
        self._lock = threading.Lock()
        self._tickets: dict[str, HydrationTicket] = {}
        self._pending: dict[str, list[HydrationTicket]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._workers: list[threading.Thread] = []
        self._errors: dict[str, str] = {}
        self._closed = False

    def _tape_key(self, catalog_path: str, record: Any) -> str:
        try:
            _, instance = self.catalog.get_latest_instance_for_path(catalog_path)
            return str(instance.barcode)
        except FileNotFoundError:
            return f"vg:{record.volume_group_id}"

    def status(self, catalog_path: str) -> HydrationState:
        record = self.catalog.get_file_record(catalog_path)
        if record is not None and self.cache.is_cached(record.checksum_sha256):
            return HydrationState.ONLINE
        with self._lock:
            ticket = self._tickets.get(catalog_path)
        if ticket is not None and not ticket.done.is_set():
            return HydrationState.HYDRATING
        return HydrationState.OFFLINE

    def last_error(self, catalog_path: str) -> str | None:
        with self._lock:
            return self._errors.get(catalog_path)

    def request(self, catalog_path: str) -> HydrationTicket:
        record = self.catalog.get_file_record(catalog_path)
        if record is None:
            raise FileNotFoundError(catalog_path)
        with self._lock:
            if self._closed:
                raise HydrationFailedError("hydrator is shut down")
            existing = self._tickets.get(catalog_path)
            if existing is not None and not existing.done.is_set():
                return existing
            ticket = HydrationTicket(catalog_path, record.checksum_sha256, "")
            if self.cache.is_cached(record.checksum_sha256):
                ticket.done.set()
                return ticket
            self._tickets[catalog_path] = ticket
        # Restart-safety: a job already in flight for this path is re-attached,
        # not duplicated.
        job_id = self.engine.find_active_job(catalog_path)
        if job_id is not None:
            ticket.job_id = job_id
            self._spawn(self._resume, ticket)
            return ticket
        ticket.tape_key = self._tape_key(catalog_path, record)
        with self._lock:
            self._pending.setdefault(ticket.tape_key, []).append(ticket)
            if ticket.tape_key not in self._timers:
                timer = threading.Timer(self.batch_window_s, self._flush, args=(ticket.tape_key,))
                timer.daemon = True
                self._timers[ticket.tape_key] = timer
                timer.start()
        return ticket

    def wait(self, ticket: HydrationTicket, timeout: float | None = None) -> bytes:
        if not ticket.done.wait(timeout):
            raise HydrationTimeoutError(f"{ticket.catalog_path} still hydrating")
        if ticket.error is not None:
            raise HydrationFailedError(ticket.error)
        return self.cache.retrieve(ticket.checksum)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Cancel batches not yet started and wait for in-flight restores."""
        with self._lock:
            self._closed = True
            timers, self._timers = self._timers, {}
            pending, self._pending = self._pending, {}
            workers = list(self._workers)
        for timer in timers.values():
            timer.cancel()
        for tickets in pending.values():
            for ticket in tickets:
                self._fail(ticket, "hydration cancelled by unmount")
        for worker in workers:
            worker.join(timeout)

    # -- internals -----------------------------------------------------------
    def _spawn(self, target: Any, *args: Any) -> None:
        worker = threading.Thread(target=target, args=args, daemon=True)
        with self._lock:
            self._workers = [w for w in self._workers if w.is_alive()]
            self._workers.append(worker)
        worker.start()

    def _flush(self, tape_key: str) -> None:
        with self._lock:
            self._timers.pop(tape_key, None)
            batch = self._pending.pop(tape_key, [])
            if self._closed or not batch:
                return
            self._workers.append(threading.current_thread())
        try:
            staged = self.engine.restore_batch(tape_key, [t.catalog_path for t in batch])
        except (HydrationFailedError, OSError) as exc:
            reason = str(exc) if isinstance(exc, HydrationFailedError) else "restore I/O error"
            for ticket in batch:
                self._fail(ticket, reason)
            return
        for ticket in batch:
            self._commit(ticket, staged.get(ticket.catalog_path))

    def _resume(self, ticket: HydrationTicket) -> None:
        assert ticket.job_id is not None
        try:
            self._commit(ticket, self.engine.resume(ticket.job_id))
        except (HydrationFailedError, OSError, KeyError):
            self._fail(ticket, f"restore job {ticket.job_id} did not complete")

    def _commit(self, ticket: HydrationTicket, staged: Path | None) -> None:
        if staged is None:
            self._fail(ticket, "restore produced no file")
            return
        try:
            self.cache.store_verified(ticket.checksum, staged)
        except CacheChecksumError:
            self._fail(ticket, "restored bytes failed checksum verification")
            return
        except OSError:
            self._fail(ticket, "restore I/O error")
            return
        with self._lock:
            self._errors.pop(ticket.catalog_path, None)
        ticket.done.set()

    def _fail(self, ticket: HydrationTicket, reason: str) -> None:
        ticket.error = reason
        with self._lock:
            self._errors[ticket.catalog_path] = reason
        ticket.done.set()
