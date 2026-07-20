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
