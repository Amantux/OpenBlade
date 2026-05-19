"""Local file cache for hydrated tape content."""

from __future__ import annotations

import hashlib
from pathlib import Path


class HydrationCache:
    def __init__(self, cache_dir: str) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_key(self, checksum: str) -> Path:
        return self.cache_dir / checksum[:2] / checksum

    def is_cached(self, checksum: str) -> bool:
        return self.cache_key(checksum).exists()

    def store(self, checksum: str, data: bytes) -> Path:
        path = self.cache_key(checksum)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def retrieve(self, checksum: str) -> bytes:
        path = self.cache_key(checksum)
        if not path.exists():
            raise FileNotFoundError(f"Not in cache: {checksum}")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != checksum:
            path.unlink()
            raise ValueError(f"Cache integrity failure: expected {checksum}, got {actual}")
        return data

    def evict(self, checksum: str) -> None:
        path = self.cache_key(checksum)
        if path.exists():
            path.unlink()
