"""FUSE hydration cache integrity: open-time verify, persisted LRU index, chunked pread."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.fuse import cache as cache_mod
from openblade.fuse.cache import SAMPLE_BYTES, CacheIntegrityError, HydrationCache
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.hydration import HydrationFailedError, Hydrator
from openblade.fuse.mount import CatalogFuseOperations

BIG = bytes(range(256)) * 1024  # 256 KiB, larger than one sample block
BIG_SUM = hashlib.sha256(BIG).hexdigest()


def _rewrite_keep_stat(path: Path, mutate: bytes) -> None:
    """Replace file contents but restore mtime_ns, so only the sample hash can notice."""
    st = path.stat()
    path.write_bytes(mutate)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _flip_sampled_byte(cache: HydrationCache, checksum: str) -> None:
    path = cache.cache_key(checksum)
    data = bytearray(path.read_bytes())
    offset = cache_mod._sample_offset(checksum, len(data))
    data[offset + SAMPLE_BYTES // 2] ^= 0xFF
    _rewrite_keep_stat(path, bytes(data))


def test_verify_entry_passes_for_intact_entry(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path))
    cache.store(BIG_SUM, BIG)
    assert cache.verify_entry(BIG_SUM) is True
    assert cache.is_cached(BIG_SUM)


def test_flipped_byte_in_sample_is_detected_and_evicted(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path))
    cache.store(BIG_SUM, BIG)
    _flip_sampled_byte(cache, BIG_SUM)

    with pytest.raises(CacheIntegrityError) as exc:
        cache.verify_entry(BIG_SUM)
    assert str(tmp_path) not in str(exc.value)  # curated message, no paths
    assert not cache.is_cached(BIG_SUM)
    assert not cache.meta_key(BIG_SUM).exists()
    assert cache.used_bytes() == 0


def test_truncated_entry_is_detected_and_evicted(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path))
    cache.store(BIG_SUM, BIG)
    _rewrite_keep_stat(cache.cache_key(BIG_SUM), BIG[:1000])

    with pytest.raises(CacheIntegrityError):
        cache.verify_entry(BIG_SUM)
    assert not cache.is_cached(BIG_SUM)
    assert cache.used_bytes() == 0


def test_missing_sidecar_is_treated_as_corrupt(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path))
    cache.store(BIG_SUM, BIG)
    cache.meta_key(BIG_SUM).unlink()
    with pytest.raises(CacheIntegrityError):
        cache.verify_entry(BIG_SUM)
    assert not cache.is_cached(BIG_SUM)


# -- (c) mount open re-hydrates a corrupted entry instead of EIO ---------------


class _Engine:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.batches: list[list[str]] = []

    def restore_batch(self, tape_key: str, catalog_paths: list[str]) -> dict[str, Path]:
        self.batches.append(sorted(catalog_paths))
        out = {}
        for path in catalog_paths:
            dest = self.staging / path.strip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(BIG)
            out[path] = dest
        return out

    def find_active_job(self, catalog_path: str) -> str | None:
        return None

    def resume(self, job_id: str, catalog_path: str | None = None) -> Path:
        raise HydrationFailedError(job_id)


def test_mount_open_rehydrates_corrupted_entry(tmp_path: Path) -> None:
    init_db(f"sqlite:///{tmp_path / 'cat.db'}")
    repo = CatalogRepository(get_session())
    group = repo.create_volume_group("g")
    rec = repo.create_file_record("/big.bin", len(BIG), BIG_SUM, group.id)
    inst = repo.create_file_instance(rec.id, "TAPE01", "/big.bin")
    repo.mark_instance_archived(inst.id)
    fs = CatalogFilesystem(repo, cache_dir=str(tmp_path / "cache"))
    engine = _Engine(tmp_path / "staging")
    hyd = Hydrator(repo, fs.cache, engine, batch_window_s=0.01)  # type: ignore[arg-type]
    ops = CatalogFuseOperations(fs, data_plane=hyd, hydrate_timeout=5.0)

    fh = ops.open("/big.bin", os.O_RDONLY)
    ops.release("/big.bin", fh)
    assert len(engine.batches) == 1

    _flip_sampled_byte(fs.cache, BIG_SUM)

    fh = ops.open("/big.bin", os.O_RDONLY)  # must not raise EIO
    assert ops.read("/big.bin", len(BIG), 0, fh) == BIG
    ops.release("/big.bin", fh)
    assert len(engine.batches) == 2  # went back through the data plane


# -- (d) restart keeps the byte budget honest ---------------------------------


def _payloads() -> list[tuple[str, bytes]]:
    out = []
    for i in range(3):
        data = bytes([i]) * 1000
        out.append((hashlib.sha256(data).hexdigest(), data))
    return out


def _on_disk_bytes(cache: HydrationCache) -> int:
    return sum(cache.cache_key(c).stat().st_size for c, _ in _payloads() if cache.is_cached(c))


@pytest.mark.parametrize("drop_index", [False, True])
def test_restart_counts_on_disk_bytes_and_enforces_budget(tmp_path: Path, drop_index: bool) -> None:
    first = HydrationCache(str(tmp_path))
    for checksum, data in _payloads():
        first.store(checksum, data)
    if drop_index:
        first.index_path.unlink()  # scan must still find every file

    reopened = HydrationCache(str(tmp_path))
    assert reopened.used_bytes() == _on_disk_bytes(reopened) == 3000

    budgeted = HydrationCache(str(tmp_path), max_bytes=1500)
    assert budgeted.used_bytes() == _on_disk_bytes(budgeted) <= 1500
    assert budgeted.used_bytes() > 0


def test_corrupt_index_is_rebuilt_from_scan(tmp_path: Path) -> None:
    first = HydrationCache(str(tmp_path))
    for checksum, data in _payloads():
        first.store(checksum, data)
    first.index_path.write_text("{not json", encoding="utf-8")
    assert HydrationCache(str(tmp_path)).used_bytes() == 3000


def test_index_drops_entries_whose_file_is_gone(tmp_path: Path) -> None:
    first = HydrationCache(str(tmp_path))
    for checksum, data in _payloads():
        first.store(checksum, data)
    first.cache_key(_payloads()[0][0]).unlink()
    assert HydrationCache(str(tmp_path)).used_bytes() == 2000


# -- (e) read_range survives short preads -------------------------------------


def test_read_range_loops_over_short_preads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = HydrationCache(str(tmp_path))
    cache.store(BIG_SUM, BIG)
    real_pread = os.pread
    calls = threading.local()
    calls.n = 0

    def short_pread(fd: int, length: int, offset: int) -> bytes:
        calls.n += 1
        return real_pread(fd, min(length, 7), offset)

    monkeypatch.setattr(cache_mod.os, "pread", short_pread)
    assert cache.read_range(BIG_SUM, 3, 100) == BIG[3:103]
    assert calls.n > 1
    # Past EOF: exactly the bytes available.
    assert cache.read_range(BIG_SUM, len(BIG) - 10, 100) == BIG[-10:]
