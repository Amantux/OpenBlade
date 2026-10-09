"""FUSE data plane: hydration, cache, batching, safety (no kernel FUSE needed)."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import CartridgeOfflineError
from openblade.fuse.cache import CacheEntryInUseError, HydrationCache
from openblade.fuse.filesystem import CatalogFilesystem
from openblade.fuse.hydration import (
    HydrationFailedError,
    HydrationTimeoutError,
    Hydrator,
    JobRestoreEngine,
)
from openblade.fuse.mount import STATE_XATTR, CatalogFuseOperations

FILES = {
    "/a.bin": (b"alpha" * 100, "TAPE01"),
    "/b.bin": (b"bravo" * 50, "TAPE01"),
    "/c.bin": (b"charlie", "TAPE02"),
}


class FakeEngine:
    """Stands in for the restore job path; records one call per tape batch."""

    def __init__(self, staging: Path, *, corrupt: bool = False) -> None:
        self.staging = staging
        self.corrupt = corrupt
        self.batches: list[tuple[str, list[str]]] = []
        self.gate = threading.Event()
        self.gate.set()

    def restore_batch(self, tape_key: str, catalog_paths: list[str]) -> dict[str, Path]:
        self.gate.wait(10)
        self.batches.append((tape_key, sorted(catalog_paths)))
        out = {}
        for path in catalog_paths:
            dest = self.staging / path.strip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"half" if self.corrupt else FILES[path][0])
            out[path] = dest
        return out

    def find_active_job(self, catalog_path: str) -> str | None:
        return None

    def resume(self, job_id: str) -> Path:
        raise HydrationFailedError(job_id)


@pytest.fixture
def catalog(tmp_path: Path) -> CatalogRepository:
    init_db(f"sqlite:///{tmp_path / 'cat.db'}")
    repo = CatalogRepository(get_session())
    group = repo.create_volume_group("g")
    for path, (data, barcode) in FILES.items():
        rec = repo.create_file_record(path, len(data), hashlib.sha256(data).hexdigest(), group.id)
        inst = repo.create_file_instance(rec.id, barcode, path)
        repo.mark_instance_archived(inst.id)
    return repo


def _plane(catalog: CatalogRepository, tmp_path: Path, **kw: object):  # type: ignore[no-untyped-def]
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    engine = FakeEngine(tmp_path / "staging", corrupt=bool(kw.pop("corrupt", False)))
    hyd = Hydrator(catalog, fs.cache, engine, batch_window_s=0.05)
    ops = CatalogFuseOperations(fs, data_plane=hyd, hydrate_timeout=5.0, **kw)  # type: ignore[arg-type]
    return ops, hyd, engine


def test_offline_read_hydrates_and_bytes_match(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, _, _ = _plane(catalog, tmp_path)
    assert ops.getattr("/a.bin")["st_size"] == len(FILES["/a.bin"][0])  # real size while offline
    assert ops.getxattr("/a.bin", STATE_XATTR) == b"offline"
    assert ops.listxattr("/a.bin") == [STATE_XATTR]
    fh = ops.open("/a.bin", os.O_RDONLY)
    assert ops.read("/a.bin", 10_000, 0, fh) == FILES["/a.bin"][0]
    assert ops.getxattr("/a.bin", STATE_XATTR) == b"online"


def test_concurrent_requests_share_one_ticket(catalog, tmp_path):  # type: ignore[no-untyped-def]
    _, hyd, engine = _plane(catalog, tmp_path)
    engine.gate.clear()
    t1, t2 = hyd.request("/a.bin"), hyd.request("/a.bin")
    assert t1 is t2
    assert hyd.status("/a.bin").value == "hydrating"
    engine.gate.set()
    hyd.wait(t1, 5)
    assert len(engine.batches) == 1


def test_nonblocking_open_returns_eagain_then_succeeds(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, hyd, engine = _plane(catalog, tmp_path, blocking=False)
    engine.gate.clear()
    with pytest.raises(OSError) as exc:
        ops.open("/a.bin", os.O_RDONLY)
    assert exc.value.errno == errno.EAGAIN
    engine.gate.set()
    hyd.wait(hyd.request("/a.bin"), 5)
    assert ops.open("/a.bin", os.O_RDONLY) > 0


def test_blocking_open_times_out_with_eagain(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, hyd, engine = _plane(catalog, tmp_path)
    ops.hydrate_timeout = 0.2
    engine.gate.clear()
    with pytest.raises(OSError) as exc:
        ops.open("/a.bin", os.O_RDONLY)
    assert exc.value.errno == errno.EAGAIN
    engine.gate.set()
    hyd.shutdown()


def test_eviction_never_drops_an_open_file(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, hyd, _ = _plane(catalog, tmp_path)
    fh = ops.open("/a.bin", os.O_RDONLY)
    checksum = catalog.get_file_record("/a.bin").checksum_sha256  # type: ignore[union-attr]
    with pytest.raises(CacheEntryInUseError):
        hyd.cache.evict(checksum)
    hyd.cache.max_bytes = 1  # budget pressure must skip the open file too
    ops.open("/c.bin", os.O_RDONLY)
    assert hyd.cache.is_cached(checksum)
    ops.release("/a.bin", fh)
    assert not hyd.cache.is_cached(checksum)  # released -> evictable under budget


def test_failed_restore_leaves_file_offline_without_partial(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, hyd, _ = _plane(catalog, tmp_path, corrupt=True)
    with pytest.raises(OSError) as exc:
        ops.open("/a.bin", os.O_RDONLY)
    assert exc.value.errno == errno.EIO
    assert ops.getxattr("/a.bin", STATE_XATTR) == b"offline"
    assert hyd.last_error("/a.bin") == "restored bytes failed checksum verification"
    leftovers = [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]
    assert leftovers == []  # no half file under the checksum name or a temp name


def test_same_tape_batching(catalog, tmp_path):  # type: ignore[no-untyped-def]
    _, hyd, engine = _plane(catalog, tmp_path)
    tickets = [hyd.request(p) for p in ("/a.bin", "/b.bin", "/c.bin")]
    for t in tickets:
        hyd.wait(t, 5)
    assert sorted(engine.batches) == [("TAPE01", ["/a.bin", "/b.bin"]), ("TAPE02", ["/c.bin"])]


def test_restart_rederives_ticket_from_job_state(catalog, tmp_path):  # type: ignore[no-untyped-def]
    dest = tmp_path / "staged" / "a"
    job = catalog.create_job("restore", {"catalog_path": "/a.bin", "dest_path": str(dest)})
    cache = HydrationCache(str(tmp_path / "cache"))
    hyd = Hydrator(catalog, cache, JobRestoreEngine(None, catalog, tmp_path), batch_window_s=0.05)
    ticket = hyd.request("/a.bin")
    assert ticket.job_id == job.id  # re-attached, not a second restore
    dest.parent.mkdir(parents=True)
    dest.write_bytes(FILES["/a.bin"][0])
    job.state = "completed"
    catalog.session.commit()
    assert hyd.wait(ticket, 5) == FILES["/a.bin"][0]


def test_unmount_cancels_pending_batches(catalog, tmp_path):  # type: ignore[no-untyped-def]
    ops, hyd, engine = _plane(catalog, tmp_path)
    hyd.batch_window_s = 30
    ticket = hyd.request("/a.bin")
    ops.destroy("/")
    assert ticket.done.is_set() and ticket.error == "hydration cancelled by unmount"
    assert engine.batches == []


def test_gateway_close_of_unpinned_open_does_not_unpin_other_handle(catalog, tmp_path):  # type: ignore[no-untyped-def]
    from openblade.nas.protocol_gateway import ProtocolGateway

    _, hyd, engine = _plane(catalog, tmp_path)
    gw = ProtocolGateway()
    gw.attach_hydrator(hyd)
    checksum = catalog.get_file_record("/a.bin").checksum_sha256  # type: ignore[union-attr]
    engine.gate.clear()
    assert gw.on_open("/a.bin").value == "hydrating"  # client B opens mid-hydration
    engine.gate.set()
    hyd.wait(hyd.request("/a.bin"), 5)
    assert gw.on_open("/a.bin").value == "online"  # client A opens the online file

    gw.on_close("/a.bin")  # client B closes; A still holds the file open
    hyd.cache.max_bytes = 1
    gw.on_open("/c.bin", timeout=5)  # budget pressure

    assert hyd.cache.is_cached(checksum)


class _RaisingEngine(FakeEngine):
    def __init__(self, staging: Path, exc: Exception) -> None:
        super().__init__(staging)
        self.exc = exc

    def restore_batch(self, tape_key: str, catalog_paths: list[str]) -> dict[str, Path]:
        raise self.exc


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (CartridgeOfflineError("cartridge TAPE01 is offline"), "cartridge TAPE01 is offline"),
        (RuntimeError("dsn=postgres://u:secret@db"), "restore failed unexpectedly"),
    ],
)
def test_untyped_engine_error_fails_ticket_and_file_goes_offline(catalog, tmp_path, exc, expected):  # type: ignore[no-untyped-def]
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    hyd = Hydrator(catalog, fs.cache, _RaisingEngine(tmp_path / "s", exc), batch_window_s=0.01)
    ticket = hyd.request("/a.bin")
    assert ticket.done.wait(3), "worker died without completing the ticket"
    assert ticket.error == expected
    assert "secret" not in (hyd.last_error("/a.bin") or "")
    assert hyd.status("/a.bin").value == "offline"


def test_request_unregisters_ticket_when_job_lookup_raises(catalog, tmp_path):  # type: ignore[no-untyped-def]
    fs = CatalogFilesystem(catalog, cache_dir=str(tmp_path / "cache"))
    engine = FakeEngine(tmp_path / "s")
    engine.find_active_job = lambda p: (_ for _ in ()).throw(RuntimeError("db down"))  # type: ignore[method-assign]
    hyd = Hydrator(catalog, fs.cache, engine, batch_window_s=0.01)
    with pytest.raises(RuntimeError):
        hyd.request("/a.bin")
    assert hyd.status("/a.bin").value == "offline"


def test_resume_sees_job_completed_by_another_session(catalog, tmp_path):  # type: ignore[no-untyped-def]
    job = catalog.create_job("restore", {"catalog_path": "/a.bin"})
    engine = JobRestoreEngine(
        None, catalog, tmp_path / "s", poll_interval_s=0.02, resume_timeout_s=3.0
    )
    assert catalog.get_job(job.id).state == "pending"  # load into the identity map
    other = CatalogRepository(get_session())
    row = other.get_job(job.id)
    row.metadata_json = json.dumps({"catalog_path": "/a.bin", "dest_path": str(tmp_path / "x")})
    row.state = "completed"
    other.session.commit()
    assert engine.resume(str(job.id)) == tmp_path / "x"


def test_resume_gives_up_at_deadline_with_typed_error(catalog, tmp_path):  # type: ignore[no-untyped-def]
    job = catalog.create_job("restore", {"catalog_path": "/a.bin"})
    engine = JobRestoreEngine(
        None, catalog, tmp_path / "s", poll_interval_s=0.02, resume_timeout_s=0.2
    )
    with pytest.raises(HydrationTimeoutError):
        engine.resume(str(job.id))
