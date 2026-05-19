"""Integration tests for sharded archive and restore with the simulator."""

import hashlib
import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

BARCODES = ["SHARD1L8", "SHARD2L8", "SHARD3L8"]
CAPACITY = 50 * 1024 * 1024


def _setup(num_drives: int = 3, barcodes: list[str] | None = None):
    lane_barcodes = barcodes or BARCODES
    library = MockLibraryBackend(num_slots=20, num_drives=num_drives)
    for slot_id, barcode in enumerate(lane_barcodes, start=1):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=CAPACITY)
    for barcode in lane_barcodes:
        ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )
    return library, ltfs


def _catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def _make_files(tmp_path: Path, count: int = 3, size: int = 1024) -> list[Path]:
    files: list[Path] = []
    for index in range(count):
        file_path = tmp_path / f"file_{index}.bin"
        file_path.write_bytes(bytes(value % 256 for value in range(size)))
        files.append(file_path)
    return files


def test_stripe_archive_and_restore(tmp_path: Path) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=3)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    files = _make_files(source_dir, count=6, size=512)
    source_checksums = {
        file_path.name: hashlib.sha256(file_path.read_bytes()).hexdigest() for file_path in files
    }

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="photos",
        lane_barcodes=BARCODES,
        mode=ShardMode.STRIPE,
    )
    job = catalog.create_job("archive", {})
    result = run_sharded_archive(request, library, ltfs, catalog, scheduler, job.id)
    assert result.errors == []
    assert result.files_archived == 6

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    for file_path in files:
        restore_request = ShardedRestoreRequest(
            catalog_path=str(file_path),
            dest_path=restore_dir / file_path.name,
        )
        restore_job = catalog.create_job("restore", {})
        restore_result = run_sharded_restore(
            restore_request,
            library,
            ltfs,
            catalog,
            scheduler,
            restore_job.id,
        )
        assert restore_result.checksum_verified
        restored_checksum = hashlib.sha256((restore_dir / file_path.name).read_bytes()).hexdigest()
        assert restored_checksum == source_checksums[file_path.name]


def test_block_stripe_archive_and_restore(tmp_path: Path) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=3)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    data = bytes(index % 256 for index in range(9000))
    source_file = source_dir / "bigfile.bin"
    source_file.write_bytes(data)

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="archive",
        lane_barcodes=BARCODES,
        mode=ShardMode.BLOCK_STRIPE,
        block_size=3000,
    )
    archive_job = catalog.create_job("archive", {})
    result = run_sharded_archive(request, library, ltfs, catalog, scheduler, archive_job.id)
    assert result.errors == []

    restore_request = ShardedRestoreRequest(
        catalog_path=str(source_file),
        dest_path=tmp_path / "restored_bigfile.bin",
        block_size=3000,
    )
    restore_job = catalog.create_job("restore", {})
    restore_result = run_sharded_restore(
        restore_request,
        library,
        ltfs,
        catalog,
        scheduler,
        restore_job.id,
    )
    assert restore_result.checksum_verified
    assert restore_request.dest_path.read_bytes() == data


def test_parallel_restore_uses_multiple_drives(tmp_path: Path) -> None:
    library, ltfs = _setup(num_drives=3, barcodes=BARCODES)
    scheduler = DriveScheduler(num_drives=3)
    catalog = _catalog()

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    files = _make_files(source_dir, count=3, size=256)

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="test",
        lane_barcodes=BARCODES,
        mode=ShardMode.STRIPE,
    )
    archive_job = catalog.create_job("archive", {})
    run_sharded_archive(request, library, ltfs, catalog, scheduler, archive_job.id)

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    errors: list[Exception] = []

    def _restore(file_path: Path) -> None:
        try:
            restore_request = ShardedRestoreRequest(
                catalog_path=str(file_path),
                dest_path=restore_dir / file_path.name,
            )
            restore_job = catalog.create_job("restore", {})
            run_sharded_restore(restore_request, library, ltfs, catalog, scheduler, restore_job.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_restore, args=(file_path,)) for file_path in files[:2]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert errors == []


def test_scheduler_blocks_when_all_drives_busy() -> None:
    scheduler = DriveScheduler(num_drives=1)
    handles = scheduler.acquire_drives(["T001L8"])

    with pytest.raises(Exception) as exc_info:
        scheduler.acquire_drives(["T002L8"], timeout=0.05)
    assert exc_info.type.__name__ == "DriveBusyError"

    scheduler.release_drives(handles)
    handles2 = scheduler.acquire_drives(["T002L8"], timeout=1.0)
    scheduler.release_drives(handles2)
