"""Semantics of the read-only FUSE mount, plus a real mount on this host.

The operations-object tests need no FUSE at all (the ops object is plain Python
by design); the live-mount test needs ``/dev/fuse`` and the optional extra and
skips cleanly without them.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.mount import (
    WRITE_OPERATIONS,
    CatalogFuseOperations,
    FuseUnavailableError,
    load_fuse,
    mount_catalog,
)

HELLO = b"hello fuse\n"
HELLO_SHA = hashlib.sha256(HELLO).hexdigest()


@pytest.fixture()
def catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    repo = CatalogRepository(get_session())
    group = repo.create_volume_group("photos")
    record = repo.create_file_record("/photos/a.txt", len(HELLO), HELLO_SHA, group.id)
    repo.create_file_instance(record.id, "PHO001L8", "/photos/a.txt")
    repo.create_file_record("/photos/2024/b.txt", 3, "b" * 64, group.id)
    return repo


@pytest.fixture()
def filesystem(catalog: CatalogRepository, tmp_path: Path) -> CatalogFilesystem:
    return CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))


# --------------------------------------------------------------------------
# operations semantics
# --------------------------------------------------------------------------


def test_readdir_matches_catalog_tree(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    assert sorted(ops("readdir", "/photos", 0)) == [".", "..", "2024", "a.txt"]
    assert sorted(ops("readdir", "/photos/2024", 0)) == [".", "..", "b.txt"]


def test_getattr_uses_catalog_metadata(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    attrs = ops("getattr", "/photos/a.txt", None)
    assert attrs["st_size"] == len(HELLO)
    assert attrs["st_mode"] & 0o777 == 0o444
    directory = ops("getattr", "/photos", None)
    assert directory["st_mode"] & 0o170000 == 0o040000
    assert directory["st_mode"] & 0o222 == 0  # no write bits anywhere


def test_getattr_unknown_path_is_enoent(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    with pytest.raises(OSError) as excinfo:
        ops("getattr", "/photos/missing.txt", None)
    assert excinfo.value.errno == errno.ENOENT


@pytest.mark.parametrize("operation", sorted(WRITE_OPERATIONS))
def test_every_write_operation_is_erofs(filesystem: CatalogFilesystem, operation: str) -> None:
    ops = CatalogFuseOperations(filesystem)
    with pytest.raises(OSError) as excinfo:
        ops(operation, "/photos/a.txt", b"data")
    assert excinfo.value.errno == errno.EROFS


def test_write_operations_are_visible_as_attributes(filesystem: CatalogFilesystem) -> None:
    """fusepy only installs handlers for ops that exist as attributes.

    Without the attribute the kernel answers ENOSYS and our explicit read-only
    refusal never runs, so this is part of the EROFS contract, not cosmetics.
    """
    ops = CatalogFuseOperations(filesystem)
    for operation in WRITE_OPERATIONS:
        assert getattr(ops, operation, None) is not None
    with pytest.raises(AttributeError):
        _ = ops.definitely_not_an_operation


def test_open_for_write_is_erofs(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    with pytest.raises(OSError) as excinfo:
        ops("open", "/photos/a.txt", os.O_WRONLY)
    assert excinfo.value.errno == errno.EROFS


def test_uncached_read_is_eio_and_names_restore(
    filesystem: CatalogFilesystem, caplog: pytest.LogCaptureFixture
) -> None:
    ops = CatalogFuseOperations(filesystem)
    with caplog.at_level("WARNING"), pytest.raises(OSError) as excinfo:
        ops("open", "/photos/a.txt", os.O_RDONLY)
    assert excinfo.value.errno == errno.EIO
    assert "openblade restore" in caplog.text


def test_cached_read_returns_bytes(filesystem: CatalogFilesystem) -> None:
    filesystem.cache.store(HELLO_SHA, HELLO)
    ops = CatalogFuseOperations(filesystem)
    handle = ops("open", "/photos/a.txt", os.O_RDONLY)
    assert ops("read", "/photos/a.txt", 5, 0, handle) == HELLO[:5]
    assert ops("read", "/photos/a.txt", 1024, 5, handle) == HELLO[5:]
    ops("release", "/photos/a.txt", handle)


def test_hydrate_flag_materialises_through_the_hydrator(filesystem: CatalogFilesystem) -> None:
    calls: list[str] = []

    def hydrator(path: str) -> bytes:
        calls.append(path)
        return HELLO

    ops = CatalogFuseOperations(filesystem, hydrator=hydrator)
    handle = ops("open", "/photos/a.txt", os.O_RDONLY)
    assert ops("read", "/photos/a.txt", 1024, 0, handle) == HELLO
    assert calls == ["/photos/a.txt"]


def test_hydration_failure_is_eio_not_a_crash(filesystem: CatalogFilesystem) -> None:
    def hydrator(path: str) -> bytes:
        raise RuntimeError("tape drive is on fire")

    ops = CatalogFuseOperations(filesystem, hydrator=hydrator)
    with pytest.raises(OSError) as excinfo:
        ops("open", "/photos/a.txt", os.O_RDONLY)
    assert excinfo.value.errno == errno.EIO


def test_path_traversal_is_rejected(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    with pytest.raises(OSError) as excinfo:
        ops("getattr", "/photos/../../etc/shadow", None)
    assert excinfo.value.errno in {errno.EACCES, errno.ENOENT}


def test_unknown_operation_is_enosys(filesystem: CatalogFilesystem) -> None:
    ops = CatalogFuseOperations(filesystem)
    with pytest.raises(OSError) as excinfo:
        ops("bmap", "/photos/a.txt", 1, 1)
    assert excinfo.value.errno == errno.ENOSYS


# --------------------------------------------------------------------------
# degradation when the optional extra is missing
# --------------------------------------------------------------------------


def test_load_fuse_reports_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    def fake_import(name: str, package: str | None = None) -> object:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(FuseUnavailableError) as excinfo:
        load_fuse()
    assert "openblade[fuse]" in str(excinfo.value)


def test_mount_rejects_a_non_directory(filesystem: CatalogFilesystem, tmp_path: Path) -> None:
    target = tmp_path / "not-a-dir"
    target.write_text("x")
    with pytest.raises((NotADirectoryError, FuseUnavailableError)):
        mount_catalog(filesystem, str(target))


def test_cli_mount_degrades_without_the_extra(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from typer.testing import CliRunner

    from openblade.cli import fuse_and_health
    from openblade.cli.main import app

    def explode(*args: object, **kwargs: object) -> None:
        raise FuseUnavailableError("FUSE support is not installed. openblade[fuse]")

    monkeypatch.setattr(fuse_and_health, "mount_catalog", explode)
    monkeypatch.setenv("OPENBLADE_DB_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    result = CliRunner().invoke(app, ["fuse", "mount", str(tmp_path)])
    assert result.exit_code == 1
    assert "openblade[fuse]" in result.output


# --------------------------------------------------------------------------
# live mount on this host
# --------------------------------------------------------------------------

_MOUNT_SCRIPT = """
import sys
from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.mount import mount_catalog

db_url, cache_dir, mountpoint, hydrate, content = sys.argv[1:6]
init_db(db_url)
catalog = CatalogRepository(get_session())
filesystem = CatalogFilesystem(catalog, cache_dir=cache_dir)
payload = content.encode("latin-1")
hydrator = (lambda path: payload) if hydrate == "yes" else None
mount_catalog(filesystem, mountpoint, hydrator=hydrator)
"""


def _fuse_available() -> bool:
    if not Path("/dev/fuse").exists():
        return False
    try:
        load_fuse()
    except FuseUnavailableError:
        return False
    return shutil.which("fusermount") is not None or shutil.which("fusermount3") is not None


requires_fuse = pytest.mark.skipif(
    not _fuse_available(),
    reason="needs /dev/fuse, a fusermount binary and the optional 'fuse' extra",
)


def _unmount(mountpoint: Path) -> None:
    for command in (["fusermount", "-u", str(mountpoint)], ["umount", str(mountpoint)]):
        if shutil.which(command[0]) is None:
            continue
        if subprocess.run(command, capture_output=True, check=False).returncode == 0:
            return


def _wait_for_mount(mountpoint: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = (process.stderr.read() if process.stderr else b"").decode()
            pytest.fail(f"mount process exited early: {stderr}")
        if os.path.ismount(mountpoint):
            return
        time.sleep(0.2)
    pytest.fail("mount did not appear within 20s")


@pytest.fixture()
def live_mount(tmp_path: Path, request: pytest.FixtureRequest):
    """Seed a catalog, mount it in a subprocess, yield the mountpoint."""

    def _mount(*, hydrate: bool, cached: bool) -> Path:
        db_path = tmp_path / "live.db"
        db_url = f"sqlite:///{db_path}"
        init_db(db_url)
        repo = CatalogRepository(get_session())
        group = repo.create_volume_group("live")
        record = repo.create_file_record("/live/a.txt", len(HELLO), HELLO_SHA, group.id)
        repo.create_file_instance(record.id, "LIV001L8", "/live/a.txt")
        repo.create_file_record("/live/nested/b.txt", 3, "b" * 64, group.id)

        cache_dir = tmp_path / "cache"
        if cached:
            from openblade.fuse.cache import HydrationCache

            HydrationCache(str(cache_dir)).store(HELLO_SHA, HELLO)

        mountpoint = tmp_path / "mnt"
        mountpoint.mkdir(exist_ok=True)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _MOUNT_SCRIPT,
                db_url,
                str(cache_dir),
                str(mountpoint),
                "yes" if hydrate else "no",
                HELLO.decode("latin-1"),
            ],
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        request.addfinalizer(lambda: (_unmount(mountpoint), process.wait(timeout=20)))
        _wait_for_mount(mountpoint, process)
        return mountpoint

    return _mount


@requires_fuse
def test_live_mount_tree_and_stat(live_mount) -> None:
    mountpoint = live_mount(hydrate=False, cached=True)
    assert sorted(os.listdir(mountpoint)) == ["live"]
    assert sorted(os.listdir(mountpoint / "live")) == ["a.txt", "nested"]
    info = os.stat(mountpoint / "live" / "a.txt")
    assert info.st_size == len(HELLO)
    assert info.st_mode & 0o777 == 0o444
    assert (mountpoint / "live" / "a.txt").read_bytes() == HELLO


@requires_fuse
def test_live_mount_rejects_writes_with_erofs(live_mount) -> None:
    mountpoint = live_mount(hydrate=False, cached=True)
    with pytest.raises(OSError) as excinfo:
        (mountpoint / "live" / "new.txt").write_text("nope")
    assert excinfo.value.errno == errno.EROFS
    with pytest.raises(OSError) as excinfo:
        os.mkdir(mountpoint / "live" / "newdir")
    assert excinfo.value.errno == errno.EROFS
    with pytest.raises(OSError) as excinfo:
        os.unlink(mountpoint / "live" / "a.txt")
    assert excinfo.value.errno == errno.EROFS


@requires_fuse
def test_live_mount_uncached_read_is_eio(live_mount) -> None:
    mountpoint = live_mount(hydrate=False, cached=False)
    with pytest.raises(OSError) as excinfo:
        (mountpoint / "live" / "a.txt").read_bytes()
    assert excinfo.value.errno == errno.EIO


@requires_fuse
def test_live_mount_hydrate_flag_serves_the_file(live_mount) -> None:
    mountpoint = live_mount(hydrate=True, cached=False)
    assert (mountpoint / "live" / "a.txt").read_bytes() == HELLO


@requires_fuse
def test_live_mount_unmounts_cleanly(live_mount, tmp_path: Path) -> None:
    mountpoint = live_mount(hydrate=False, cached=True)
    assert os.path.ismount(mountpoint)
    _unmount(mountpoint)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and os.path.ismount(mountpoint):
        time.sleep(0.2)
    assert not os.path.ismount(mountpoint)
    assert os.listdir(mountpoint) == []
