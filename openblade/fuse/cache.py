"""Local file cache for hydrated tape content."""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path


class CacheEntryInUseError(RuntimeError):
    """Raised when eviction is asked to drop a file that still has open handles."""


class CacheChecksumError(ValueError):
    """Raised when staged bytes do not match the catalog checksum."""


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
        self._track(checksum, final.stat().st_size)
        return final

    def cache_key(self, checksum: str) -> Path:
        return self.cache_dir / checksum[:2] / checksum

    def is_cached(self, checksum: str) -> bool:
        return self.cache_key(checksum).exists()

    def store(self, checksum: str, data: bytes) -> Path:
        path = self.cache_key(checksum)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(data)
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
            raise ValueError(f"Cache integrity failure: expected {checksum}, got {actual}")
        return data

    def evict(self, checksum: str) -> None:
        with self._lock:
            if self._refs.get(checksum, 0) > 0:
                raise CacheEntryInUseError(f"{checksum} has open handles")
            self._lru.pop(checksum, None)
            path = self.cache_key(checksum)
            if path.exists():
                path.unlink()
