"""Local file cache for hydrated tape content."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

from openblade.domain.errors import OpenBladeError

logger = logging.getLogger(__name__)

SAMPLE_BYTES = 64 * 1024
_PREAD_CAP = 1 << 30  # 1 GiB per os.pread call


class CacheEntryInUseError(RuntimeError):
    """Raised when eviction is asked to drop a file that still has open handles."""


class CacheError(OpenBladeError, ValueError):
    """Typed cache failure for the protocol boundary (ValueError kept for old callers)."""


class CacheChecksumError(CacheError):
    """Raised when staged bytes do not match the catalog checksum."""


class CacheIntegrityError(CacheError):
    """Raised when a cached entry no longer matches its integrity record (it is evicted)."""


def _sample_offset(checksum: str, size: int) -> int:
    """Deterministic sample offset derived from the checksum; 0 when the file fits in one block."""
    if size <= SAMPLE_BYTES:
        return 0
    return int(checksum[:8], 16) % max(1, size - SAMPLE_BYTES)


class HydrationCache:
    """Checksum-addressed staging cache.

    ``max_bytes`` turns on a byte budget with LRU eviction. Entries with open
    handles (``acquire``/``release`` refcount) are never evicted; the budget may
    be exceeded rather than drop a file a reader is using.
    """

    def __init__(self, cache_dir: str, max_bytes: int | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._lru: OrderedDict[str, int] = OrderedDict()
        self._refs: dict[str, int] = {}
        with self._lock:
            # Not persisted here: a fresh cache stays empty on disk, and the scan
            # re-finds any unindexed files on the next start. Evictions persist.
            self._load_index()
            self._enforce_budget()

    # -- persisted LRU index -------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.cache_dir / "index.json"

    def _load_index(self) -> None:
        """Rebuild ``_lru`` from ``index.json`` plus any on-disk entries it misses.

        Entries whose file is gone are dropped; files not in the index are appended
        (size from stat) so a restart always counts on-disk bytes against the budget.
        """
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                for checksum, size in raw:
                    if isinstance(checksum, str) and self.cache_key(checksum).is_file():
                        self._lru[checksum] = int(size)
            except (OSError, ValueError, TypeError):
                logger.warning("fuse cache: index.json unreadable; rebuilding from disk scan")
                self._lru.clear()
        for path in sorted(self.cache_dir.glob("??/*")):
            name = path.name
            if name.startswith(".") or name.endswith(".meta.json") or not path.is_file():
                continue
            if name not in self._lru:
                self._lru[name] = path.stat().st_size

    def _persist_index(self) -> None:
        tmp = self.index_path.with_name(f".index.json.{os.getpid()}.{threading.get_ident()}.part")
        try:
            tmp.write_text(json.dumps([[c, n] for c, n in self._lru.items()]), encoding="utf-8")
            os.replace(tmp, self.index_path)
        except OSError:
            logger.warning("fuse cache: could not persist index.json")
        finally:
            tmp.unlink(missing_ok=True)

    # -- refcounting / LRU ---------------------------------------------------
    def acquire(self, checksum: str) -> None:
        with self._lock:
            self._refs[checksum] = self._refs.get(checksum, 0) + 1
            if checksum in self._lru:
                self._lru.move_to_end(checksum)

    def release(self, checksum: str) -> None:
        with self._lock:
            count = self._refs.get(checksum, 0) - 1
            if count <= 0:
                self._refs.pop(checksum, None)
            else:
                self._refs[checksum] = count
            self._enforce_budget()

    def open_count(self, checksum: str) -> int:
        with self._lock:
            return self._refs.get(checksum, 0)

    def used_bytes(self) -> int:
        with self._lock:
            return sum(self._lru.values())

    def _track(self, checksum: str, size: int) -> None:
        with self._lock:
            self._lru[checksum] = size
            self._lru.move_to_end(checksum)
            self._enforce_budget()
            self._persist_index()

    def _enforce_budget(self) -> None:
        if self.max_bytes is None:
            return
        # The newest entry is exempt: it was just stored for a reader about to open it.
        for checksum in list(self._lru)[:-1]:
            if sum(self._lru.values()) <= self.max_bytes:
                return
            if self._refs.get(checksum, 0) > 0:
                continue  # never evict an open file
            self.evict(checksum)

    def store_verified(self, checksum: str, staged: Path) -> Path:
        """Move ``staged`` into the cache only if its sha256 matches.

        Bytes are written to a temp name beside the final key and renamed only
        after the checksum verifies, so a failed restore never leaves a partial
        file under the checksum name.
        """
        final = self.cache_key(checksum)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f".{checksum}.{os.getpid()}.{threading.get_ident()}.part")
        digest = hashlib.sha256()
        try:
            with staged.open("rb") as src, tmp.open("wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    digest.update(chunk)
                    dst.write(chunk)
            if digest.hexdigest() != checksum:
                raise CacheChecksumError(f"staged bytes for {checksum} failed verification")
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)
        self._write_meta(checksum)
        self._track(checksum, final.stat().st_size)
        return final

    # -- integrity records ---------------------------------------------------
    def meta_key(self, checksum: str) -> Path:
        path = self.cache_key(checksum)
        return path.with_name(f"{path.name}.meta.json")

    def _sample_digest(self, path: Path, offset: int, length: int) -> str:
        with path.open("rb") as handle:
            handle.seek(offset)
            return hashlib.sha256(handle.read(length)).hexdigest()

    def _write_meta(self, checksum: str) -> None:
        """Record size, mtime_ns and a sampled block hash beside the cached file."""
        path = self.cache_key(checksum)
        st = path.stat()
        offset = _sample_offset(checksum, st.st_size)
        length = min(SAMPLE_BYTES, st.st_size)
        record = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sample": {
                "offset": offset,
                "len": length,
                "sha256": self._sample_digest(path, offset, length),
            },
        }
        meta = self.meta_key(checksum)
        tmp = meta.with_name(f".{meta.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            tmp.write_text(json.dumps(record), encoding="utf-8")
            os.replace(tmp, meta)
        finally:
            tmp.unlink(missing_ok=True)

    def _entry_matches(self, checksum: str) -> bool:
        path = self.cache_key(checksum)
        try:
            record = json.loads(self.meta_key(checksum).read_text(encoding="utf-8"))
            st = path.stat()
            sample = record["sample"]
            if st.st_size != record["size"] or st.st_mtime_ns != record["mtime_ns"]:
                return False
            offset, length = int(sample["offset"]), int(sample["len"])
            if (offset, length) != (
                _sample_offset(checksum, st.st_size),
                min(SAMPLE_BYTES, st.st_size),
            ):
                return False
            return bool(self._sample_digest(path, offset, length) == sample["sha256"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def verify_entry(self, checksum: str) -> bool:
        """Cheap open-time check: size + mtime_ns + one sampled 64 KiB block.

        On mismatch or a missing/unreadable integrity record the entry (file and
        sidecar) is dropped and ``CacheIntegrityError`` raised, so the caller can
        fall through to re-hydration.
        """
        with self._lock:
            if self._entry_matches(checksum):
                return True
            self._lru.pop(checksum, None)
            self.cache_key(checksum).unlink(missing_ok=True)
            self.meta_key(checksum).unlink(missing_ok=True)
            self._persist_index()
        raise CacheIntegrityError("cached entry failed integrity verification and was evicted")

    def cache_key(self, checksum: str) -> Path:
        return self.cache_dir / checksum[:2] / checksum

    def is_cached(self, checksum: str) -> bool:
        return self.cache_key(checksum).exists()

    def store(self, checksum: str, data: bytes) -> Path:
        path = self.cache_key(checksum)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(data)
        self._write_meta(checksum)
        self._track(checksum, len(data))
        return path

    def retrieve(self, checksum: str) -> bytes:
        path = self.cache_key(checksum)
        if not path.exists():
            raise FileNotFoundError(f"Not in cache: {checksum}")
        with path.open("rb") as handle:
            data = handle.read()
        actual = hashlib.sha256(data).hexdigest()
        if actual != checksum:
            path.unlink()
            raise CacheChecksumError(f"Cache integrity failure: expected {checksum}, got {actual}")
        return data

    def read_range(self, checksum: str, offset: int, length: int) -> bytes:
        """``pread`` ``[offset, offset+length)`` of a cached file without re-hashing it.

        The whole file was hashed once by ``store_verified``/``store`` callers; per-range
        reads must stay O(length), so integrity is not re-checked here (``retrieve`` does).
        """
        if offset < 0 or length < 0:
            raise CacheError(f"invalid range offset={offset} length={length}")
        path = self.cache_key(checksum)
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            raise FileNotFoundError(f"Not in cache: {checksum}") from None
        try:
            # pread may return short (and Linux caps one call near 2 GiB): loop until
            # ``length`` bytes or EOF, returning exactly the bytes available.
            parts: list[bytes] = []
            remaining = length
            pos = offset
            while remaining > 0:
                chunk = os.pread(fd, min(remaining, _PREAD_CAP), pos)
                if not chunk:
                    break  # EOF
                parts.append(chunk)
                pos += len(chunk)
                remaining -= len(chunk)
            return b"".join(parts)
        finally:
            os.close(fd)

    def evict(self, checksum: str) -> None:
        with self._lock:
            if self._refs.get(checksum, 0) > 0:
                raise CacheEntryInUseError(f"{checksum} has open handles")
            self._lru.pop(checksum, None)
            path = self.cache_key(checksum)
            if path.exists():
                path.unlink()
            self.meta_key(checksum).unlink(missing_ok=True)
            self._persist_index()
