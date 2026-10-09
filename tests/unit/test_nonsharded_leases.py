"""Non-sharded run_archive_job / run_restore_job take drives through DriveScheduler leases."""

from __future__ import annotations

import functools
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import DriveBusyError, StaleLeaseError
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.jobs.scheduler import CatalogLeaseStore, DriveScheduler
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges


def _stack(
    tmp_path: Path,
) -> tuple[CatalogRepository, CatalogRepository, MockLibraryBackend, MockLTFSBackend]:
    # Two sessions on one file DB model two processes sharing one lease store.
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    repo_a, repo_b = CatalogRepository(get_session()), CatalogRepository(get_session())
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    library.unload(0, 1)
    group = repo_a.create_volume_group("photos")
    repo_a.add_barcode_to_volume_group(group.id, barcode)
    return repo_a, repo_b, library, ltfs


def _archive(
    repo: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    source: Path,
    scheduler: DriveScheduler,
) -> None:
    job = repo.create_job("archive", {"source_path": str(source), "volume_group": "photos"})
    run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        repo,
        job.id,
        scheduler=scheduler,
    )


def test_archive_holding_drive_0_blocks_restore_until_released(tmp_path: Path) -> None:
    repo_a, repo_b, library, ltfs = _stack(tmp_path)
    sched_a = DriveScheduler(1, store=CatalogLeaseStore(repo_a), job_id="archive")
    first = tmp_path / "a.txt"
    first.write_text("first")
    _archive(repo_a, library, ltfs, first, sched_a)

    sched_b = DriveScheduler(1, store=CatalogLeaseStore(repo_b), job_id="restore")
    sched_b.acquire_drives = functools.partial(sched_b.acquire_drives, timeout=0.2)  # type: ignore[method-assign]
    restore_job = repo_b.create_job("restore", {})
    blocked: list[BaseException] = []
    real_write = ltfs.write_file

    def write_then_contend(handle: Any, src: Path, dest: PurePosixPath) -> Any:
        result = real_write(handle, src, dest)
        with pytest.raises(DriveBusyError) as caught:
            run_restore_job(
                RestoreRequest("/photos/a.txt", tmp_path / "blocked.txt"),
                library,
                ltfs,
                repo_b,
                restore_job.id,
                scheduler=sched_b,
            )
        blocked.append(caught.value)
        return result

    ltfs.write_file = write_then_contend  # type: ignore[method-assign]
    second = tmp_path / "b.txt"
    second.write_text("second")
    _archive(repo_a, library, ltfs, second, sched_a)
    ltfs.write_file = real_write  # type: ignore[method-assign]
    assert len(blocked) == 1
    assert not (tmp_path / "blocked.txt").exists()

    # Released after the archive finished: the same restore now gets drive 0.
    result = run_restore_job(
        RestoreRequest("/photos/a.txt", tmp_path / "restored.txt"),
        library,
        ltfs,
        repo_b,
        repo_b.create_job("restore", {}).id,
        scheduler=sched_b,
    )
    assert result.checksum_verified
    assert (tmp_path / "restored.txt").read_text() == "first"
    events = [entry.event for entry in repo_a.job_journal(repo_a.list_jobs()[1].id)]
    assert events.count("lease_acquired") == 1
    assert events.count("lease_released") == 1


def test_stale_lease_aborts_archive_without_unmount_or_unload(tmp_path: Path) -> None:
    repo_a, repo_b, library, ltfs = _stack(tmp_path)
    sched_a = DriveScheduler(1, store=CatalogLeaseStore(repo_a), job_id="archive")
    other_store = CatalogLeaseStore(repo_b)
    calls: list[str] = []
    real_write, real_unmount, real_unload = ltfs.write_file, ltfs.unmount, library.unload

    def write_then_lose_lease(handle: Any, src: Path, dest: PurePosixPath) -> Any:
        result = real_write(handle, src, dest)
        # Another process reclaims the lease (e.g. TTL expiry) mid-job.
        other_store.release([lease.id for lease in other_store.live_leases()])
        return result

    def record_unmount(handle: Any) -> None:
        calls.append("unmount")
        real_unmount(handle)

    def record_unload(*args: Any, **kwargs: Any) -> Any:
        calls.append("unload")
        return real_unload(*args, **kwargs)

    ltfs.write_file = write_then_lose_lease  # type: ignore[method-assign]
    ltfs.unmount = record_unmount  # type: ignore[method-assign]
    library.unload = record_unload  # type: ignore[method-assign]
    source = tmp_path / "a.txt"
    source.write_text("payload")
    job = repo_a.create_job("archive", {"source_path": str(source), "volume_group": "photos"})
    with pytest.raises(StaleLeaseError):
        run_archive_job(
            ArchiveRequest(source_path=source, volume_group_name="photos"),
            library,
            ltfs,
            repo_a,
            job.id,
            scheduler=sched_a,
        )
    assert calls == []
    refreshed = repo_a.get_job(job.id)
    assert refreshed is not None and refreshed.state == "failed"
