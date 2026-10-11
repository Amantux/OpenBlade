"""Catalog-backed drive leases (docs/decisions/2026-10-09-persistent-drive-leases.md)."""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import DriveBusyError, StaleLeaseError
from openblade.domain.models import DriveLease
from openblade.jobs.scheduler import CatalogLeaseStore, DriveScheduler, InMemoryLeaseStore


def _two_repos(tmp_path: Path) -> tuple[CatalogRepository, CatalogRepository]:
    # A file DB with two independent sessions models two processes (CLI + API)
    # sharing one catalog; an in-memory StaticPool DB would share one connection.
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    return CatalogRepository(get_session()), CatalogRepository(get_session())


def test_two_schedulers_on_one_catalog_cannot_both_hold_drive_0(tmp_path: Path) -> None:
    repo_a, repo_b = _two_repos(tmp_path)
    sched_a = DriveScheduler(1, store=CatalogLeaseStore(repo_a), job_id="job-a")
    sched_b = DriveScheduler(1, store=CatalogLeaseStore(repo_b), job_id="job-b")

    held = sched_a.acquire_drives(["AAA001L9"], timeout=1.0)
    assert held[0].drive_id == 0

    with pytest.raises(DriveBusyError):
        sched_b.acquire_drives(["BBB001L9"], timeout=0.6)
    assert sched_b.status() == {0: "AAA001L9"}

    sched_a.release_drives(held)
    got = sched_b.acquire_drives(["BBB001L9"], timeout=1.0)
    assert got[0].drive_id == 0
    assert got[0].fencing_token > held[0].fencing_token


def test_fencing_tokens_strictly_increase_and_are_never_reissued(tmp_path: Path) -> None:
    repo, _ = _two_repos(tmp_path)
    sched = DriveScheduler(2, store=CatalogLeaseStore(repo), job_id="job-1")

    seen: list[int] = []
    for _ in range(3):
        handles = sched.acquire_drives(["AAA001L9", "AAA002L9"], timeout=1.0)
        seen.extend(h.fencing_token for h in handles)
        sched.release_drives(handles)

    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen)


def test_verify_raises_stale_lease_after_release_or_expiry(tmp_path: Path) -> None:
    repo, other = _two_repos(tmp_path)
    sched = DriveScheduler(2, store=CatalogLeaseStore(repo), job_id="job-1")
    (handle,) = sched.acquire_drives(["AAA001L9"], timeout=1.0)
    sched.verify(handle)  # live
    sched.heartbeat([handle])
    sched.verify(handle)

    assert other.release_leases_for_job("job-1") == 1
    with pytest.raises(StaleLeaseError):
        sched.verify(handle)

    expired = DriveScheduler(
        2, store=CatalogLeaseStore(repo), job_id="job-2", ttl=timedelta(seconds=-1)
    )
    (dead,) = expired.acquire_drives(["AAA002L9"], timeout=1.0)
    with pytest.raises(StaleLeaseError):
        expired.verify(dead)


def test_held_leases_are_kept_alive_without_explicit_heartbeats() -> None:
    # A single long tape write must not outlive its own lease: the scheduler
    # heartbeats in the background for as long as a handle is held.
    store = InMemoryLeaseStore()
    scheduler = DriveScheduler(num_drives=1, store=store, ttl=timedelta(seconds=0.3))
    handles = scheduler.acquire_drives(["MCK00001"])
    time.sleep(0.8)  # > 2 TTLs, with no heartbeat() call from the "job"
    scheduler.verify(handles[0])  # still live
    scheduler.release_drives(handles)
    time.sleep(0.4)
    assert store.live_leases() == []  # released, and the thread did not revive it


TTL = timedelta(seconds=60)


def test_excluded_drive_is_skipped_in_one_call(tmp_path: Path) -> None:
    repo, _ = _two_repos(tmp_path)
    leases = repo.acquire_drive_leases(
        job_id="j", barcodes=["A00001L9"], num_drives=2, ttl=TTL, exclude=frozenset({0})
    )
    assert leases is not None and [lease.drive_id for lease in leases] == [1]
    assert (
        CatalogLeaseStore(repo).acquire(
            job_id="k", barcodes=["B"], num_drives=2, ttl=TTL, exclude=frozenset({0})
        )
        is None
    )


@pytest.mark.parametrize("drive_id", [0, -1])
def test_acquire_drive_lease_at_free_then_held(tmp_path: Path, drive_id: int) -> None:
    repo, other = _two_repos(tmp_path)
    lease = repo.acquire_drive_lease_at(job_id="j", drive_id=drive_id, barcode="A", ttl=TTL)
    assert lease is not None and lease.drive_id == drive_id
    assert other.acquire_drive_lease_at(job_id="k", drive_id=drive_id, barcode="B", ttl=TTL) is None
    repo.release_leases([lease.id])
    assert other.acquire_drive_lease_at(job_id="k", drive_id=drive_id, barcode="B", ttl=TTL)


def test_in_memory_acquire_drive_lease_at_is_exclusive() -> None:
    store = InMemoryLeaseStore()
    assert store.acquire_drive_lease_at(job_id="j", drive_id=-1, barcode="A", ttl=TTL)
    assert store.acquire_drive_lease_at(job_id="k", drive_id=-1, barcode="B", ttl=TTL) is None


def test_two_sessions_racing_for_one_drive_id_exactly_one_wins(tmp_path: Path) -> None:
    import threading

    repo_a, repo_b = _two_repos(tmp_path)
    for _ in range(20):
        barrier = threading.Barrier(2)
        results: list[DriveLease | None] = []

        def grab(
            repo: CatalogRepository,
            job: str,
            barrier: threading.Barrier = barrier,
            results: list[DriveLease | None] = results,
        ) -> None:
            barrier.wait()
            results.append(
                repo.acquire_drive_lease_at(job_id=job, drive_id=3, barcode=job, ttl=TTL)
            )

        threads = [
            threading.Thread(target=grab, args=(repo_a, "a")),
            threading.Thread(target=grab, args=(repo_b, "b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        repo_a.release_leases([winners[0].id])
