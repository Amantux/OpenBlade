"""Read-only virtual filesystem backed by the catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError
from openblade.fuse.cache import HydrationCache


@dataclass
class VirtualDirEntry:
    name: str
    is_dir: bool
    size_bytes: int
    path: PurePosixPath


class CatalogFilesystem:
    """Read-only virtual filesystem backed by the catalog."""

    def __init__(self, catalog: CatalogRepository, cache_dir: str = ".openblade-cache") -> None:
        self.catalog = catalog
        self.cache = HydrationCache(cache_dir)

    def listdir(self, path: str) -> list[VirtualDirEntry]:
        normalized = PurePosixPath(path)
        if str(normalized) == ".":
            normalized = PurePosixPath("/")
        entries: dict[str, VirtualDirEntry] = {}
        for record in self.catalog.list_file_records(str(normalized)):
            record_path = PurePosixPath(record.path)
            try:
                relative = record_path.relative_to(normalized)
            except ValueError:
                continue
            if not relative.parts:
                continue
            name = relative.parts[0]
            child_path = normalized / name if str(normalized) != "/" else PurePosixPath("/") / name
            if len(relative.parts) == 1:
                entries[name] = VirtualDirEntry(
                    name=name,
                    is_dir=False,
                    size_bytes=record.size_bytes,
                    path=child_path,
                )
            elif name not in entries:
                entries[name] = VirtualDirEntry(
                    name=name, is_dir=True, size_bytes=0, path=child_path
                )
        return sorted(entries.values(), key=lambda entry: (not entry.is_dir, entry.name))

    def stat(self, path: str) -> VirtualDirEntry | None:
        normalized = PurePosixPath(path)
        record = self.catalog.get_file_record(str(normalized))
        if record is not None:
            return VirtualDirEntry(
                name=normalized.name or "/",
                is_dir=False,
                size_bytes=record.size_bytes,
                path=normalized,
            )
        if str(normalized) == "/":
            return VirtualDirEntry(name="/", is_dir=True, size_bytes=0, path=PurePosixPath("/"))
        children = self.listdir(str(normalized))
        if children:
            return VirtualDirEntry(name=normalized.name, is_dir=True, size_bytes=0, path=normalized)
        return None

    def is_hydrated(self, path: str) -> bool:
        record = self.catalog.get_file_record(path)
        return bool(record and self.cache.is_cached(record.checksum_sha256))

    def read(self, path: str, dest: str) -> bytes:
        record = self.catalog.get_file_record(path)
        if record is None:
            raise FileNotFoundError(path)
        if not self.cache.is_cached(record.checksum_sha256):
            raise CartridgeOfflineError(
                f"{path} is offline; queue hydration before reading through the virtual filesystem"
            )
        data = self.cache.retrieve(record.checksum_sha256)
        destination = Path(dest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return data

    def write(self, path: str, data: bytes) -> None:
        del path, data
        raise PermissionError("OpenBlade virtual filesystem is read-only")

    def delete(self, path: str) -> None:
        del path
        raise PermissionError("OpenBlade virtual filesystem is read-only")
