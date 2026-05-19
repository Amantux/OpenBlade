"""Tests for DriveScheduler."""

import threading
import time

import pytest

from openblade.domain.errors import DriveBusyError
from openblade.jobs.scheduler import DriveScheduler


def test_acquire_single_drive() -> None:
    sched = DriveScheduler(num_drives=4)
    handles = sched.acquire_drives(["TAPE01L8"])
    assert len(handles) == 1
    assert handles[0].barcode == "TAPE01L8"
    assert handles[0].drive_id in range(4)
    sched.release_drives(handles)
    assert sched.available_count() == 4


def test_acquire_n_drives_atomically() -> None:
    sched = DriveScheduler(num_drives=4)
    handles = sched.acquire_drives(["T001L8", "T002L8", "T003L8"])
    assert len(handles) == 3
    assert sched.available_count() == 1
    sched.release_drives(handles)
    assert sched.available_count() == 4


def test_acquire_more_than_exist_raises() -> None:
    sched = DriveScheduler(num_drives=2)
    with pytest.raises(DriveBusyError):
        sched.acquire_drives(["T1", "T2", "T3"])


def test_acquire_duplicate_barcodes_raises() -> None:
    sched = DriveScheduler(num_drives=4)
    with pytest.raises(ValueError):
        sched.acquire_drives(["T001L8", "T001L8"])


def test_release_makes_drives_available() -> None:
    sched = DriveScheduler(num_drives=2)
    handles = sched.acquire_drives(["T001L8", "T002L8"])
    assert sched.available_count() == 0
    sched.release_drives(handles)
    assert sched.available_count() == 2


def test_acquire_waits_for_release() -> None:
    """Second acquisition blocks until first releases."""
    sched = DriveScheduler(num_drives=2)
    first = sched.acquire_drives(["T001L8", "T002L8"])
    assert sched.available_count() == 0

    results: list[list[object]] = []

    def _acquire() -> None:
        handles = sched.acquire_drives(["T003L8"], timeout=5.0)
        results.append(handles)
        sched.release_drives(handles)

    thread = threading.Thread(target=_acquire)
    thread.start()
    time.sleep(0.05)
    assert results == []
    sched.release_drives(first)
    thread.join(timeout=3.0)
    assert len(results) == 1


def test_timeout_raises_drive_busy() -> None:
    sched = DriveScheduler(num_drives=1)
    handles = sched.acquire_drives(["T001L8"])
    with pytest.raises(DriveBusyError):
        sched.acquire_drives(["T002L8"], timeout=0.05)
    sched.release_drives(handles)


def test_status_shows_allocations() -> None:
    sched = DriveScheduler(num_drives=3)
    handles = sched.acquire_drives(["ALPHA01L8"])
    status = sched.status()
    assert "ALPHA01L8" in status.values()
    sched.release_drives(handles)
