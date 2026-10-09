"""Staged (STAGING -> VERIFYING -> one bulk ARCHIVED) sharded archive + cleanup journaling."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select

from openblade.catalog.models import FileInstance
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.sharded_archive import PHYSICAL_STATE_UNKNOWN, run_sharded_archive
from tests.integration.test_sharded_archive_atomicity import (
    BARCODES,
    _catalog,
    _request,
    _setup,
    _source,
)


def _states(catalog: Any) -> set[str]:
    return {str(i.state) for i in catalog.session.scalars(select(FileInstance)).all()}


def _events(catalog: Any, job_id: str) -> list[str]:
    return [entry.event for entry in catalog.job_journal(job_id)]


def test_second_lane_write_failure_stages_but_never_archives(tmp_path: Path, monkeypatch) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    real_write, real_unmount = ltfs.write_file, ltfs.unmount
    calls = {"write": 0}
    unmounted: list[Any] = []
    lock = threading.Lock()

    def write(*args: Any, **kwargs: Any) -> Any:
        with lock:
            calls["write"] += 1
            nth = calls["write"]
        if nth == 2:
            raise OSError("lane 2 write failed")
        return real_write(*args, **kwargs)

    def unmount(mount: Any) -> Any:
        unmounted.append(mount)
        return real_unmount(mount)

    monkeypatch.setattr(ltfs, "write_file", write)
    monkeypatch.setattr(ltfs, "unmount", unmount)
    job = catalog.create_job("archive", {})
    result = run_sharded_archive(
        _request(_source(tmp_path)), library, ltfs, catalog, DriveScheduler(num_drives=2), job.id
    )

    assert result.errors and result.files_archived == 0
    states = _states(catalog)
    assert states and states <= {"staging", "verifying"}, states  # nothing ARCHIVED
    events = _events(catalog, job.id)
    assert "failed" in events and "committed" not in events
    assert len(unmounted) == 2  # both lanes unmounted on the error path
    assert all(d.barcode is None for d in library.inventory().drives)  # and unloaded


def test_unmount_raise_in_cleanup_is_journaled_and_other_drive_unloaded(
    tmp_path: Path, monkeypatch
) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    real_unmount = ltfs.unmount

    def unmount(mount: Any) -> Any:
        if str(mount.barcode) == BARCODES[0]:
            raise RuntimeError("stuck")
        return real_unmount(mount)

    monkeypatch.setattr(ltfs, "unmount", unmount)
    real_unload = library.unload
    unload_requests: list[int] = []

    def unload(drive_id: int, target_slot: int) -> Any:
        unload_requests.append(drive_id)
        return real_unload(drive_id, target_slot)

    monkeypatch.setattr(library, "unload", unload)
    job = catalog.create_job("archive", {})
    run_sharded_archive(
        _request(_source(tmp_path)), library, ltfs, catalog, DriveScheduler(num_drives=2), job.id
    )

    stored = catalog.get_job(job.id)
    assert stored is not None and PHYSICAL_STATE_UNKNOWN in str(stored.error)
    rows = [e for e in catalog.job_journal(job.id) if e.event == "physical_state_unknown"]
    assert rows and rows[0].detail["barcode"] == BARCODES[0]
    drives = {str(d.barcode): d.drive_id for d in library.inventory().drives if d.barcode}
    in_drives = set(drives)
    assert BARCODES[1] not in in_drives  # the OTHER drive was still unloaded
    # Never unload while LTFS is mounted: the stuck drive must not even be asked.
    assert BARCODES[0] in in_drives
    assert drives[BARCODES[0]] not in unload_requests
    assert "archived" not in _states(catalog)
