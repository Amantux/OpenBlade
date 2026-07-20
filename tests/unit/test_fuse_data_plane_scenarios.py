"""Tape-NAS data-plane behavioral scenarios (roadmap Phase 6, review #9).

Exercises the hydration lifecycle of the in-process virtual filesystem + hydration
cache — the logic a real SMB/NFS/FUSE mount would sit on top of: offline-tape
errors, hydrate-then-read, eviction forcing re-hydration, concurrent opens of an
offline file, and read-only enforcement. (A real containerized SMB/NFS client
harness is a follow-up once the mount is wired to libfuse; the FS is currently an
in-process model, so the meaningful behaviors are tested here directly.)
"""

from __future__ import annotations

import concurrent.futures
import hashlib
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError
from openblade.fuse.filesystem import CatalogFilesystem

DATA = b"tape-resident payload"
CHECKSUM = hashlib.sha256(DATA).hexdigest()


@pytest.fixture()
def fs(tmp_path: Path) -> CatalogFilesystem:
    init_db("sqlite:///:memory:")
    repo = CatalogRepository(get_session())
    group = repo.create_volume_group("vg-1")
    repo.create_file_record("/data/file.bin", len(DATA), CHECKSUM, group.id)
    return CatalogFilesystem(repo, cache_dir=str(tmp_path / "cache"))


def test_offline_read_errors_until_hydrated_then_succeeds(fs: CatalogFilesystem, tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    # Offline (not cached): a read must fail clearly, not hang or fabricate.
    with pytest.raises(CartridgeOfflineError):
        fs.read("/data/file.bin", str(dest))
    assert not fs.is_hydrated("/data/file.bin")

    # Hydrate (as a hydration job would), then the read serves real bytes to dest.
    fs.cache.store(CHECKSUM, DATA)
    assert fs.is_hydrated("/data/file.bin")
    assert fs.read("/data/file.bin", str(dest)) == DATA
    assert dest.read_bytes() == DATA


def test_eviction_forces_rehydration(fs: CatalogFilesystem, tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    fs.cache.store(CHECKSUM, DATA)
    assert fs.read("/data/file.bin", str(dest)) == DATA

    fs.cache.evict(CHECKSUM)  # cache pressure evicts the file

    assert not fs.is_hydrated("/data/file.bin")
    with pytest.raises(CartridgeOfflineError):  # must re-hydrate, not serve stale
        fs.read("/data/file.bin", str(dest))


def test_concurrent_reads_of_offline_file_error_consistently(fs: CatalogFilesystem, tmp_path: Path) -> None:
    def _attempt(index: int) -> str:
        try:
            fs.read("/data/file.bin", str(tmp_path / f"out-{index}.bin"))
            return "ok"
        except CartridgeOfflineError:
            return "offline"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_attempt, range(8)))
    # Every concurrent open of the offline file fails cleanly — no crash, no race
    # into a partial/served read.
    assert results == ["offline"] * 8


def test_read_missing_file_raises_not_found(fs: CatalogFilesystem, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        fs.read("/data/does-not-exist.bin", str(tmp_path / "out.bin"))


def test_virtual_filesystem_is_read_only(fs: CatalogFilesystem) -> None:
    with pytest.raises(PermissionError):
        fs.write("/data/file.bin", b"x")
    with pytest.raises(PermissionError):
        fs.delete("/data/file.bin")
