from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from openblade.domain.errors import DriveBusyError
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore

pytestmark = pytest.mark.real_hardware


@pytest.fixture
def skip_if_single_drive(drive_devices):
    if len(drive_devices) < 2:
        pytest.skip("Sharded operations require at least two drives")


@pytest.fixture
def sharded_barcodes(scratch_barcodes):
    if len(scratch_barcodes) < 2:
        pytest.skip("Sharded operations require at least two scratch barcodes")
    return scratch_barcodes[:2]


def _prepare_volume_group(context, barcodes, name: str):
    group = context.catalog.get_volume_group(name) or context.catalog.create_volume_group(name)
    for barcode in barcodes:
        context.catalog.add_barcode_to_volume_group(group.id, barcode)
    return group


def _require_sharded_context(context):
    required = ["library", "ltfs", "catalog"]
    missing = [name for name in required if not hasattr(context, name)]
    if missing:
        pytest.skip(f"Real AppContext is missing required sharded dependencies: {', '.join(missing)}")


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_stripe_two_drives(
    real_hardware_guard,
    skip_if_single_drive,
    sharded_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: at least two drives and two scratch tapes assigned to the same volume group."""
    del skip_if_single_drive
    _require_sharded_context(real_app_context)
    source_dir = tmp_path / "stripe-source"
    source_dir.mkdir()
    expected = {}
    for index in range(4):
        path = source_dir / f"stripe-{index}.bin"
        path.write_bytes((bytes([index]) * (256 * 1024)))
        expected[path] = _checksum(path)
    scheduler = DriveScheduler(num_drives=max(2, len(sharded_barcodes)))
    _prepare_volume_group(real_app_context, sharded_barcodes, "hw-stripe")
    archive_job = real_app_context.catalog.create_job("archive", {})
    result = run_sharded_archive(
        ShardedArchiveRequest(
            source_path=source_dir,
            volume_group_name="hw-stripe",
            lane_barcodes=sharded_barcodes,
            mode=ShardMode.STRIPE,
        ),
        real_app_context.library,
        real_app_context.ltfs,
        real_app_context.catalog,
        scheduler,
        archive_job.id,
    )
    assert result.errors == []
    restore_dir = tmp_path / "stripe-restore"
    restore_dir.mkdir()
    for file_path, checksum in expected.items():
        restore_job = real_app_context.catalog.create_job("restore", {})
        restore_result = run_sharded_restore(
            ShardedRestoreRequest(catalog_path=str(file_path), dest_path=restore_dir / file_path.name),
            real_app_context.library,
            real_app_context.ltfs,
            real_app_context.catalog,
            scheduler,
            restore_job.id,
        )
        assert restore_result.checksum_verified
        assert _checksum(restore_dir / file_path.name) == checksum


def test_block_stripe_two_drives(
    real_hardware_guard,
    skip_if_single_drive,
    sharded_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: at least two drives and two scratch tapes assigned to the same volume group."""
    del skip_if_single_drive
    _require_sharded_context(real_app_context)
    source_dir = tmp_path / "block-source"
    source_dir.mkdir()
    source_file = source_dir / "block-stripe.bin"
    source_file.write_bytes(b"ABCD" * (256 * 1024 * 64))
    scheduler = DriveScheduler(num_drives=max(2, len(sharded_barcodes)))
    _prepare_volume_group(real_app_context, sharded_barcodes, "hw-block-stripe")
    archive_job = real_app_context.catalog.create_job("archive", {})
    result = run_sharded_archive(
        ShardedArchiveRequest(
            source_path=source_dir,
            volume_group_name="hw-block-stripe",
            lane_barcodes=sharded_barcodes,
            mode=ShardMode.BLOCK_STRIPE,
            block_size=8 * 1024 * 1024,
        ),
        real_app_context.library,
        real_app_context.ltfs,
        real_app_context.catalog,
        scheduler,
        archive_job.id,
    )
    assert result.errors == []
    restore_job = real_app_context.catalog.create_job("restore", {})
    restore_target = tmp_path / "block-restored.bin"
    restore_result = run_sharded_restore(
        ShardedRestoreRequest(
            catalog_path=str(source_file),
            dest_path=restore_target,
            block_size=8 * 1024 * 1024,
        ),
        real_app_context.library,
        real_app_context.ltfs,
        real_app_context.catalog,
        scheduler,
        restore_job.id,
    )
    assert restore_result.checksum_verified
    assert _checksum(restore_target) == _checksum(source_file)


def test_stripe_catalog_entries(
    real_hardware_guard,
    skip_if_single_drive,
    sharded_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: at least two drives and two scratch tapes assigned to the same volume group."""
    del skip_if_single_drive
    _require_sharded_context(real_app_context)
    source_dir = tmp_path / "catalog-source"
    source_dir.mkdir()
    source_file = source_dir / "catalog.bin"
    source_file.write_bytes(b"XYZ" * (1024 * 1024))
    scheduler = DriveScheduler(num_drives=max(2, len(sharded_barcodes)))
    _prepare_volume_group(real_app_context, sharded_barcodes, "hw-catalog-stripe")
    archive_job = real_app_context.catalog.create_job("archive", {})
    run_sharded_archive(
        ShardedArchiveRequest(
            source_path=source_dir,
            volume_group_name="hw-catalog-stripe",
            lane_barcodes=sharded_barcodes,
            mode=ShardMode.BLOCK_STRIPE,
            block_size=1024 * 1024,
        ),
        real_app_context.library,
        real_app_context.ltfs,
        real_app_context.catalog,
        scheduler,
        archive_job.id,
    )
    record = real_app_context.catalog.get_file_record(str(source_file))
    assert record is not None
    assert len(record.instances) >= 2


def test_scheduler_concurrent(real_hardware_guard, drive_devices, runner):
    """Requires: at least three drives available to model a 3-drive pool."""
    del runner
    if len(drive_devices) < 3:
        pytest.skip("This scheduler contention check models a 3-drive pool")
    scheduler = DriveScheduler(num_drives=3)
    first_handles = scheduler.acquire_drives(["POOL0001", "POOL0002"])
    ready = threading.Event()
    acquired = threading.Event()
    durations = []

    def _second_request() -> None:
        ready.set()
        start = time.monotonic()
        handles = scheduler.acquire_drives(["POOL0003", "POOL0004"], timeout=2.0)
        durations.append(time.monotonic() - start)
        scheduler.release_drives(handles)
        acquired.set()

    thread = threading.Thread(target=_second_request)
    thread.start()
    ready.wait(timeout=1.0)
    time.sleep(0.2)
    assert not acquired.is_set()
    scheduler.release_drives(first_handles)
    thread.join(timeout=2.0)
    assert acquired.is_set()
    assert durations and durations[0] >= 0.2


def test_drive_scheduler_timeout(real_hardware_guard, drive_devices, runner):
    """Requires: scheduler contention to exceed the configured timeout."""
    del runner
    scheduler = DriveScheduler(num_drives=max(1, min(len(drive_devices), 2)))
    handles = scheduler.acquire_drives(["TIMEOUT1"])
    try:
        with pytest.raises((DriveBusyError, TimeoutError)):
            scheduler.acquire_drives(["TIMEOUT2", "TIMEOUT3"], timeout=0.05)
    finally:
        scheduler.release_drives(handles)
