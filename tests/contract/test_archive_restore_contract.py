"""Archive → restore byte equality through the job layer, for every backend pairing."""

from __future__ import annotations

from pathlib import Path

import pytest

from openblade.catalog.repository import CatalogRepository
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from tests.contract.conftest import BackendPair, call_with_scheduler

pytestmark = pytest.mark.contract


def _source_files(root: Path, count: int = 4) -> list[Path]:
    root.mkdir()
    files = []
    for index in range(count):
        path = root / f"file_{index}.bin"
        path.write_bytes(bytes((index + value) % 256 for value in range(700 + index)))
        files.append(path)
    return files


def test_sharded_archive_then_restore_is_byte_identical(
    backend_pair: BackendPair, catalog: CatalogRepository, tmp_path: Path
) -> None:
    backend_pair.format_all()
    lanes = backend_pair.barcodes[: min(len(backend_pair.barcodes), backend_pair.num_drives)]
    scheduler = DriveScheduler(num_drives=backend_pair.num_drives)
    files = _source_files(tmp_path / "src")
    request = ShardedArchiveRequest(
        source_path=tmp_path / "src",
        volume_group_name="contract",
        lane_barcodes=lanes,
        mode=ShardMode.STRIPE,
    )
    job = catalog.create_job("archive", {})

    result = run_sharded_archive(
        request, backend_pair.library, backend_pair.ltfs, catalog, scheduler, job.id
    )

    assert result.errors == []
    for path in files:
        dest = tmp_path / f"restored_{path.name}"
        restore_job = catalog.create_job("restore", {})
        restored = run_sharded_restore(
            ShardedRestoreRequest(catalog_path=str(path), dest_path=dest),
            backend_pair.library,
            backend_pair.ltfs,
            catalog,
            scheduler,
            restore_job.id,
        )
        assert restored.error is None
        assert restored.checksum_verified
        assert dest.read_bytes() == path.read_bytes()


def test_archive_job_then_restore_job_is_byte_identical(
    backend_pair: BackendPair, catalog: CatalogRepository, tmp_path: Path
) -> None:
    backend_pair.format_all()
    scheduler = DriveScheduler(num_drives=backend_pair.num_drives)
    files = _source_files(tmp_path / "src", count=2)
    job = catalog.create_job("archive", {})

    result = call_with_scheduler(
        run_archive_job,
        ArchiveRequest(source_path=tmp_path / "src", volume_group_name="contract"),
        backend_pair.library,
        backend_pair.ltfs,
        catalog,
        job_id=job.id,
        scheduler=scheduler,
    )

    assert result.errors == []
    for path in files:
        dest = tmp_path / f"restored_{path.name}"
        restore_job = catalog.create_job("restore", {})
        restored = call_with_scheduler(
            run_restore_job,
            RestoreRequest(catalog_path=f"/contract/{path.name}", dest_path=dest),
            backend_pair.library,
            backend_pair.ltfs,
            catalog,
            job_id=restore_job.id,
            scheduler=scheduler,
        )
        assert restored.error is None
        assert dest.read_bytes() == path.read_bytes()
