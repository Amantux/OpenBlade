"""JobQueue state is shared by every process using the same catalog file."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import ChangerBusyError, DriveOccupiedError, DriveUnreconciledError
from openblade.domain.models import JobState, JobType
from openblade.jobs.queue import JobQueue
from openblade.jobs.reconcile import PHYSICAL_STATE_UNKNOWN
from openblade.jobs.scheduler import CatalogLeaseStore


@pytest.fixture
def queues(tmp_path: Path) -> tuple[JobQueue, JobQueue]:
    # Two sessions on one file DB model two processes sharing one catalog.
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    repo_a, repo_b = CatalogRepository(get_session()), CatalogRepository(get_session())
    return JobQueue(repo_a, CatalogLeaseStore(repo_a)), JobQueue(repo_b, CatalogLeaseStore(repo_b))


def test_job_created_by_one_process_is_visible_to_another(
    queues: tuple[JobQueue, JobQueue],
) -> None:
    a, b = queues
    job = a.create_job(JobType.ARCHIVE, {"barcode": "TAPE01L8"})
    seen = b.get_job(job.id)
    assert seen.job_type is JobType.ARCHIVE
    assert seen.state is JobState.PENDING
    assert seen.metadata == {"barcode": "TAPE01L8"}


def test_drive_claim_is_exclusive_across_processes(queues: tuple[JobQueue, JobQueue]) -> None:
    a, b = queues
    job_a = a.create_job(JobType.ARCHIVE, {})
    job_b = b.create_job(JobType.ARCHIVE, {})
    a.claim_drive(0, job_a.id)
    with pytest.raises(DriveOccupiedError):
        b.claim_drive(0, job_b.id)
    b.claim_drive(1, job_b.id)
    with pytest.raises(DriveOccupiedError):
        a.claim_drive(1, job_a.id)
    a.release_drive(0, job_a.id)
    b.claim_drive(0, job_b.id)


def test_changer_claim_is_exclusive_across_processes(queues: tuple[JobQueue, JobQueue]) -> None:
    a, b = queues
    job_a = a.create_job(JobType.ARCHIVE, {})
    job_b = b.create_job(JobType.ARCHIVE, {})
    a.claim_changer(job_a.id)
    with pytest.raises(ChangerBusyError):
        b.claim_changer(job_b.id)
    # The changer pseudo-drive does not occupy a real drive.
    b.claim_drive(0, job_b.id)
    a.release_changer(job_a.id)
    b.claim_changer(job_b.id)


def test_run_job_transitions_are_visible_from_another_process(
    queues: tuple[JobQueue, JobQueue],
) -> None:
    a, b = queues
    job = a.create_job(JobType.ARCHIVE, {})
    running = threading.Event()
    proceed = threading.Event()

    def func() -> str:
        running.set()
        assert proceed.wait(timeout=5)
        return "done"

    results: list[str] = []
    worker = threading.Thread(target=lambda: results.append(a.run_job(job, func)[1]))
    worker.start()
    assert running.wait(timeout=5)
    assert b.get_job(job.id).state is JobState.RUNNING
    proceed.set()
    worker.join(timeout=5)
    assert results == ["done"]
    assert b.get_job(job.id).state is JobState.COMPLETED


@pytest.mark.parametrize("target", ["drive", "changer"])
def test_racing_claims_past_the_owner_precheck_exactly_one_wins(
    queues: tuple[JobQueue, JobQueue], monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    # Both claimers pass the "nobody owns it" pre-check before either acquires,
    # so exclusivity rests solely on the store's targeted acquisition.
    barrier = threading.Barrier(2)
    for queue in queues:
        original = queue._owner
        calls = {"n": 0}

        def gated(
            slot: int,
            original: Callable[[int], str | None] = original,
            calls: dict[str, int] = calls,
        ) -> str | None:
            calls["n"] += 1
            result = original(slot)
            if calls["n"] == 1:
                barrier.wait(timeout=5)
            return result

        monkeypatch.setattr(queue, "_owner", gated)

    outcomes: list[str] = []

    def claim(queue: JobQueue) -> None:
        job = queue.create_job(JobType.ARCHIVE, {})
        try:
            if target == "drive":
                queue.claim_drive(2, job.id)
            else:
                queue.claim_changer(job.id)
            outcomes.append("won")
        except (DriveOccupiedError, ChangerBusyError):
            outcomes.append("busy")

    threads = [threading.Thread(target=claim, args=(q,)) for q in queues]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["busy", "won"]


def test_claim_drive_refuses_unreconciled_drive(tmp_path: Path) -> None:
    """Regression S3: the public claim API honours the reconciliation barrier."""
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    repo = CatalogRepository(get_session())
    queue = JobQueue(repo, CatalogLeaseStore(repo))
    repo.journal(
        "crashed-job", PHYSICAL_STATE_UNKNOWN, {"op": "unload", "barcode": "T1L8", "drive": 2}
    )
    job = queue.create_job(JobType.ARCHIVE, {})
    with pytest.raises(DriveUnreconciledError):
        queue.claim_drive(2, job.id)
    assert all(lease.drive_id != 2 for lease in CatalogLeaseStore(repo).live_leases())
    queue.claim_drive(1, job.id)  # other drives are unaffected


def test_run_job_heartbeats_claims_past_the_ttl(tmp_path: Path) -> None:
    """Regression S3: a holder running longer than the TTL keeps its drive claim."""
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    repo_a, repo_b = CatalogRepository(get_session()), CatalogRepository(get_session())
    queue = JobQueue(repo_a, CatalogLeaseStore(repo_a), ttl=timedelta(seconds=2))
    observer = CatalogLeaseStore(repo_b)
    job = queue.create_job(JobType.ARCHIVE, {})
    queue.claim_drive(0, job.id)
    seen: list[bool] = []

    def work() -> None:
        for _ in range(10):
            time.sleep(0.5)
            repo_b.session.expire_all()
            seen.append(any(lease.job_id == job.id for lease in observer.live_leases()))

    queue.run_job(job, work)
    assert len(seen) == 10
    assert all(seen), seen
