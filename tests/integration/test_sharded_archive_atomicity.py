"""Atomicity of sharded archive (roadmap #6).

A batch that fails — whether a shard write fails, or the tapes fail to unmount
cleanly after writing — must mark NOTHING archived. Staged instances stay PENDING
(discoverable/resumable) and are never exposed as durable.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from openblade.catalog.db import get_session, init_db
from openblade.catalog.models import FileInstance
from openblade.catalog.repository import _ARCHIVED_INSTANCE_STATES, CatalogRepository
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

BARCODES = ["ATOM01L8", "ATOM02L8"]


def _setup() -> tuple[MockLibraryBackend, MockLTFSBackend]:
    library = MockLibraryBackend(num_slots=10, num_drives=2)
    for slot_id, barcode in enumerate(BARCODES, start=1):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=50 * 1024 * 1024)
    for barcode in BARCODES:
        ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    return library, ltfs


def _catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def _archived_count(catalog: CatalogRepository) -> int:
    return catalog.session.execute(
        select(func.count())
        .select_from(FileInstance)
        .where(FileInstance.state.in_(_ARCHIVED_INSTANCE_STATES))
    ).scalar_one()


def _source(tmp_path: Path, count: int = 2) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    for index in range(count):
        (src / f"f{index}.bin").write_bytes(bytes(value % 256 for value in range(256)))
    return src


def _request(src: Path) -> ShardedArchiveRequest:
    return ShardedArchiveRequest(
        source_path=src, volume_group_name="atom", lane_barcodes=BARCODES, mode=ShardMode.STRIPE
    )


def test_load_helpers_do_not_mutate_scheduler_lock_key() -> None:
    # Both the archive and restore load helpers must record the physical drive
    # WITHOUT mutating handle.drive_id (the scheduler lock key freed on release).
    from openblade.jobs.scheduler import DriveHandle
    from openblade.jobs.sharded_archive import _load_barcode
    from openblade.jobs.sharded_restore import _ensure_loaded

    for helper in (_load_barcode, _ensure_loaded):
        library, ltfs = _setup()
        slot = library.find_slot_by_barcode(BARCODES[0])
        assert slot is not None
        library.load(slot, 1)  # cartridge physically in drive 1
        handle = DriveHandle(drive_id=0, barcode=BARCODES[0])  # scheduler reserved drive 0
        physical, _slot = helper(None, library, ltfs, handle, "job")  # already-loaded fast path
        assert handle.drive_id == 0, f"{helper.__name__} mutated the scheduler lock key"
        assert handle.physical == 1 and physical == 1


def test_scheduler_lease_not_corrupted_when_tape_preloaded_elsewhere(tmp_path: Path) -> None:
    # The scheduler reserves a drive (lock key); a cartridge already loaded in a
    # DIFFERENT physical drive must not cause the reserved key to be mutated — that
    # would leak the reserved drive and free one the scheduler never held.
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=2)

    # Physically load lane-0's cartridge into drive 1 (the scheduler will reserve 0).
    slot = library.find_slot_by_barcode(BARCODES[0])
    assert slot is not None
    library.load(slot, 1)
    assert library.find_drive_by_barcode(BARCODES[0]) == 1

    request = ShardedArchiveRequest(
        source_path=_source(tmp_path, count=1),
        volume_group_name="atom",
        lane_barcodes=[BARCODES[0]],
        mode=ShardMode.STRIPE,
    )
    job = catalog.create_job("archive", {})
    result = run_sharded_archive(request, library, ltfs, catalog, scheduler, job.id)

    assert not result.errors, result.errors
    # The reserved drive (0) must be released; nothing leaked, nothing wrongly freed.
    assert scheduler.available_count() == 2, scheduler.status()
    # A subsequent job can still acquire every drive.
    handles = scheduler.acquire_drives([BARCODES[0], BARCODES[1]], timeout=1.0)
    assert len(handles) == 2
    scheduler.release_drives(handles)


def _raise(exc: Exception):
    def _fail(*args, **kwargs):
        raise exc

    return _fail


def test_write_failure_commits_nothing(tmp_path: Path, monkeypatch) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=2)
    monkeypatch.setattr(ltfs, "write_file", _raise(RuntimeError("boom")))

    job = catalog.create_job("archive", {})
    result = run_sharded_archive(_request(_source(tmp_path)), library, ltfs, catalog, scheduler, job.id)

    assert result.errors  # failure surfaced, not swallowed
    assert result.files_archived == 0
    assert _archived_count(catalog) == 0  # nothing marked archived


def test_unmount_failure_blocks_commit(tmp_path: Path, monkeypatch) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=2)
    # Writes succeed; the tapes then fail to unmount -> unknown physical state.
    monkeypatch.setattr(ltfs, "unmount", _raise(RuntimeError("stuck")))

    job = catalog.create_job("archive", {})
    result = run_sharded_archive(_request(_source(tmp_path)), library, ltfs, catalog, scheduler, job.id)

    assert result.errors  # dirty unmount surfaced as a failure
    assert result.files_archived == 0
    assert _archived_count(catalog) == 0  # data was written but the batch never committed


def test_unload_failure_blocks_commit(tmp_path: Path, monkeypatch) -> None:
    # Writes + unmount succeed, but the drive fails to UNLOAD. execute_tape_request
    # swallows unload failures unless raise_on_failed=True, so this is the path that
    # can wrongly commit a batch with a tape stuck in a drive. Must never commit.
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=2)
    monkeypatch.setattr(library, "unload", _raise(RuntimeError("drive stuck")))

    job = catalog.create_job("archive", {})
    result = run_sharded_archive(_request(_source(tmp_path)), library, ltfs, catalog, scheduler, job.id)

    assert result.errors  # stuck unload surfaced, not swallowed
    assert result.files_archived == 0
    assert _archived_count(catalog) == 0  # tape stuck in drive -> batch must not be durable
