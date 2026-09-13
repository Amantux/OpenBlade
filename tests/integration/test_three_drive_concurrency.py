"""Three jobs, three drives: disjoint drive ownership end-to-end in the simulator.

The shipped Scalar i3 profile is ``scalar-i3-50-3`` — three drives — so the
scheduler, the job queue's drive ownership and the mock library must all handle a
three-way parallel workload without two jobs ever touching the same drive.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest

from openblade.domain.errors import ChangerBusyError, DriveOccupiedError
from openblade.domain.models import JobType, MountMode
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.queue import JobQueue
from openblade.jobs.scheduler import DriveScheduler
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import scalar_i3_default

BARCODES = ["TRI001L8", "TRI002L8", "TRI003L8"]


@pytest.fixture
def three_drive_library() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library = MockLibraryBackend(num_slots=10, num_drives=3)
    for slot_id, barcode in enumerate(BARCODES, start=1):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=1_048_576)
    for barcode in BARCODES:
        ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )
    return library, ltfs


def test_shipped_simulator_profile_has_three_drives() -> None:
    library, _ = scalar_i3_default()
    assert len(library.inventory().drives) == 3


def test_simulator_drive_count_is_configurable() -> None:
    for count in (1, 2, 3, 6):
        library = MockLibraryBackend(num_slots=10, num_drives=count)
        assert [drive.drive_id for drive in library.inventory().drives] == list(range(count))


def test_three_jobs_run_on_three_drives_with_disjoint_ownership(
    three_drive_library: tuple[MockLibraryBackend, MockLTFSBackend],
) -> None:
    library, ltfs = three_drive_library
    scheduler = DriveScheduler(num_drives=3)
    queue = JobQueue()
    jobs = [queue.create_job(JobType.ARCHIVE, {"barcode": barcode}) for barcode in BARCODES]

    started = threading.Barrier(len(jobs))
    ownership: list[tuple[str, int]] = []
    errors: list[BaseException] = []
    ownership_lock = threading.Lock()

    def _run(job_id: str, barcode: str, slot_id: int) -> None:
        try:
            started.wait(timeout=5)
            handles = scheduler.acquire_drives([barcode], timeout=5.0)
            handle = handles[0]
            queue.claim_drive(handle.drive_id, job_id)
            with ownership_lock:
                ownership.append((job_id, handle.drive_id))
            try:
                # One robot, three drives: the changer serializes, the I/O does not.
                _load_with_retry(library, slot_id, handle.drive_id)
                mount = ltfs.mount(barcode, MountMode.READ_WRITE)
                ltfs.write_bytes(mount, f"/{barcode}.bin", b"x" * 1024)
                ltfs.unmount(mount)
                _unload_with_retry(library, handle.drive_id, slot_id)
            finally:
                queue.release_drive(handle.drive_id, job_id)
                scheduler.release_drives(handles)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            with ownership_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=_run, args=(job.id, barcode, slot_id))
        for job, barcode, slot_id in zip(jobs, BARCODES, range(1, 4), strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert errors == []
    assert len(ownership) == 3
    # Every job got its own drive, and all three drives were used.
    assert sorted(drive_id for _, drive_id in ownership) == [0, 1, 2]
    assert len({job_id for job_id, _ in ownership}) == 3
    # Everything is released and every tape is back in its slot with its payload.
    assert scheduler.available_count() == 3
    assert all(drive.barcode is None for drive in library.inventory().drives)
    for barcode in BARCODES:
        assert ltfs.read_bytes(barcode, f"/{barcode}.bin") == b"x" * 1024


def test_job_queue_refuses_to_share_a_drive_between_jobs() -> None:
    queue = JobQueue()
    first = queue.create_job(JobType.ARCHIVE, {})
    second = queue.create_job(JobType.ARCHIVE, {})
    queue.claim_drive(2, first.id)
    with pytest.raises(DriveOccupiedError):
        queue.claim_drive(2, second.id)
    queue.release_drive(2, first.id)
    queue.claim_drive(2, second.id)


def test_fourth_job_waits_for_a_free_drive() -> None:
    scheduler = DriveScheduler(num_drives=3)
    held = scheduler.acquire_drives(BARCODES)
    assert scheduler.available_count() == 0
    with pytest.raises(Exception) as excinfo:
        scheduler.acquire_drives(["TRI004L8"], timeout=0.05)
    assert "Timed out" in str(excinfo.value)
    scheduler.release_drives(held)
    fourth = scheduler.acquire_drives(["TRI004L8"], timeout=1.0)
    assert fourth[0].drive_id in {0, 1, 2}


def _load_with_retry(library: MockLibraryBackend, slot_id: int, drive_id: int) -> None:
    _retry_changer(lambda: library.load(slot_id, drive_id))


def _unload_with_retry(library: MockLibraryBackend, drive_id: int, slot_id: int) -> None:
    _retry_changer(lambda: library.unload(drive_id, slot_id))


def _retry_changer(action: Callable[[], object], timeout: float = 10.0) -> None:
    """The simulator models ONE robot: concurrent moves raise ChangerBusyError."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            action()
            return
        except ChangerBusyError:
            if time.monotonic() >= deadline:
                raise AssertionError("changer stayed busy for the whole retry budget") from None
            time.sleep(0.005)
