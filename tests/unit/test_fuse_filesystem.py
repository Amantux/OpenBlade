from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError
from openblade.fuse.cache import HydrationCache
from openblade.fuse.filesystem import CatalogFilesystem


@pytest.fixture()
def catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    repo = CatalogRepository(get_session())
    group = repo.create_volume_group("photos")
    record = repo.create_file_record("/photos/a.txt", 5, "2cf24dba5fb0a030e...", group.id)
    repo.create_file_instance(record.id, "PHO001L8", "/photos/a.txt")
    return repo


def test_listdir_returns_catalog_entries(catalog: CatalogRepository, tmp_path: Path) -> None:
    group = catalog.get_volume_group("photos")
    assert group is not None
    catalog.create_file_record("/photos/2024/b.txt", 1, "b", group.id)
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    entries = fs.listdir("/photos")
    assert [entry.name for entry in entries] == ["2024", "a.txt"]


def test_stat_returns_file_attrs(catalog: CatalogRepository, tmp_path: Path) -> None:
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    stat = fs.stat("/photos/a.txt")
    assert stat is not None
    assert stat.size_bytes == 5
    assert stat.is_dir is False


def test_write_raises_permission_error(catalog: CatalogRepository, tmp_path: Path) -> None:
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(PermissionError):
        fs.write("/photos/a.txt", b"data")


def test_delete_raises_permission_error(catalog: CatalogRepository, tmp_path: Path) -> None:
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(PermissionError):
        fs.delete("/photos/a.txt")


def test_read_offline_file_raises_clearly(catalog: CatalogRepository, tmp_path: Path) -> None:
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(CartridgeOfflineError):
        fs.read("/photos/a.txt", str(tmp_path / "out.txt"))


def test_cache_store_and_retrieve(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path / "cache"))
    checksum = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    cache.store(checksum, b"hello")
    assert cache.retrieve(checksum) == b"hello"


def test_cache_integrity_check(tmp_path: Path) -> None:
    cache = HydrationCache(str(tmp_path / "cache"))
    checksum = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    path = cache.store(checksum, b"hello")
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        cache.retrieve(checksum)
