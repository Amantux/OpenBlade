"""JobQueue state is shared by every process using the same catalog file."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import ChangerBusyError, DriveOccupiedError
from openblade.domain.models import JobState, JobType
from openblade.jobs.queue import JobQueue
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
