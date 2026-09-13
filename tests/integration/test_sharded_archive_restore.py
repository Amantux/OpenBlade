"""Integration tests for sharded archive and restore with the simulator."""

import hashlib
import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.sharded_restore import ShardedRestoreRequest, run_sharded_restore
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

BARCODES = ["SHARD1L8", "SHARD2L8", "SHARD3L8"]
CAPACITY = 50 * 1024 * 1024


def _setup(num_drives: int = 3, barcodes: list[str] | None = None):
    lane_barcodes = barcodes or BARCODES
    library = MockLibraryBackend(num_slots=20, num_drives=num_drives)
    for slot_id, barcode in enumerate(lane_barcodes, start=1):
        library.add_cartridge(slot_id, barcode)
    ltfs = MockLTFSBackend(library, capacity_bytes=CAPACITY)
    for barcode in lane_barcodes:
        ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )
    return library, ltfs


def _catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def _make_files(tmp_path: Path, count: int = 3, size: int = 1024) -> list[Path]:
    files: list[Path] = []
    for index in range(count):
        file_path = tmp_path / f"file_{index}.bin"
        file_path.write_bytes(bytes(value % 256 for value in range(size)))
        files.append(file_path)
    return files


def test_stripe_archive_and_restore(tmp_path: Path) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=3)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    files = _make_files(source_dir, count=6, size=512)
    source_checksums = {
        file_path.name: hashlib.sha256(file_path.read_bytes()).hexdigest() for file_path in files
    }

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="photos",
        lane_barcodes=BARCODES,
        mode=ShardMode.STRIPE,
    )
    job = catalog.create_job("archive", {})
    result = run_sharded_archive(request, library, ltfs, catalog, scheduler, job.id)
    assert result.errors == []
    assert result.files_archived == 6

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    for file_path in files:
        restore_request = ShardedRestoreRequest(
            catalog_path=str(file_path),
            dest_path=restore_dir / file_path.name,
        )
        restore_job = catalog.create_job("restore", {})
        restore_result = run_sharded_restore(
            restore_request,
            library,
            ltfs,
            catalog,
            scheduler,
            restore_job.id,
        )
        assert restore_result.checksum_verified
        restored_checksum = hashlib.sha256((restore_dir / file_path.name).read_bytes()).hexdigest()
        assert restored_checksum == source_checksums[file_path.name]


def test_block_stripe_archive_and_restore(tmp_path: Path) -> None:
    library, ltfs = _setup()
    catalog = _catalog()
    scheduler = DriveScheduler(num_drives=3)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    data = bytes(index % 256 for index in range(9000))
    source_file = source_dir / "bigfile.bin"
    source_file.write_bytes(data)

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="archive",
        lane_barcodes=BARCODES,
        mode=ShardMode.BLOCK_STRIPE,
        block_size=3000,
    )
    archive_job = catalog.create_job("archive", {})
    result = run_sharded_archive(request, library, ltfs, catalog, scheduler, archive_job.id)
    assert result.errors == []

    restore_request = ShardedRestoreRequest(
        catalog_path=str(source_file),
        dest_path=tmp_path / "restored_bigfile.bin",
        block_size=3000,
    )
    restore_job = catalog.create_job("restore", {})
    restore_result = run_sharded_restore(
        restore_request,
        library,
        ltfs,
        catalog,
        scheduler,
        restore_job.id,
    )
    assert restore_result.checksum_verified
    assert restore_request.dest_path.read_bytes() == data


def test_parallel_restore_uses_multiple_drives(tmp_path: Path) -> None:
    library, ltfs = _setup(num_drives=3, barcodes=BARCODES)
    scheduler = DriveScheduler(num_drives=3)
    catalog = _catalog()

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    files = _make_files(source_dir, count=3, size=256)

    request = ShardedArchiveRequest(
        source_path=source_dir,
        volume_group_name="test",
        lane_barcodes=BARCODES,
        mode=ShardMode.STRIPE,
    )
    archive_job = catalog.create_job("archive", {})
    run_sharded_archive(request, library, ltfs, catalog, scheduler, archive_job.id)

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    errors: list[Exception] = []

    def _restore(file_path: Path) -> None:
        try:
            restore_request = ShardedRestoreRequest(
                catalog_path=str(file_path),
                dest_path=restore_dir / file_path.name,
            )
            restore_job = catalog.create_job("restore", {})
            run_sharded_restore(restore_request, library, ltfs, catalog, scheduler, restore_job.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_restore, args=(file_path,)) for file_path in files[:2]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert errors == []


def test_scheduler_blocks_when_all_drives_busy() -> None:
    scheduler = DriveScheduler(num_drives=1)
    handles = scheduler.acquire_drives(["T001L8"])

    with pytest.raises(Exception) as exc_info:
        scheduler.acquire_drives(["T002L8"], timeout=0.05)
    assert exc_info.type.__name__ == "DriveBusyError"

    scheduler.release_drives(handles)
    handles2 = scheduler.acquire_drives(["T002L8"], timeout=1.0)
    scheduler.release_drives(handles2)


# --- Real-data campaign regression -------------------------------------------
# docs/runbooks/real-data-campaign.md: STRIPE wrote every file to
# /stripe/{basename}, a single flat namespace. Two same-named files from
# different source directories that landed on the same lane overwrote each other
# silently, and BOTH were marked archived. On the rig, alpha/same.txt and
# beta/same.txt were both catalogued at OB0001L8:/stripe/same.txt; restoring
# alpha returned beta's bytes and only the checksum verify caught it.


def _same_named_tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    for sub in ("alpha", "beta"):
        (root / sub).mkdir(parents=True)
        (root / sub / "same.txt").write_bytes(f"content from {sub}\n".encode() * 10)
    return root


def test_stripe_preserves_the_source_tree_in_the_tape_path(tmp_path: Path) -> None:
    library, ltfs = _setup(num_drives=1, barcodes=["SHARD1L8"])
    catalog = _catalog()
    root = _same_named_tree(tmp_path)
    job = catalog.create_job("archive", {"source_path": str(root)})

    result = run_sharded_archive(
        ShardedArchiveRequest(
            source_path=root,
            volume_group_name="collide",
            lane_barcodes=["SHARD1L8"],
            mode=ShardMode.STRIPE,
        ),
        library,
        ltfs,
        catalog,
        DriveScheduler(num_drives=1),
        job.id,
    )

    assert result.errors == []
    assert result.files_archived == 2

    tape_paths = sorted(
        instance.tape_path
        for record in catalog.list_file_records("/")
        for instance in record.instances
        if str(record.path).endswith("same.txt")
    )
    assert tape_paths == ["/stripe/alpha/same.txt", "/stripe/beta/same.txt"], (
        "both files share one on-tape location; the second overwrote the first"
    )
    assert len(set(tape_paths)) == 2


def test_stripe_round_trips_both_same_named_files(tmp_path: Path) -> None:
    """The proof that matters: each file restores to its own bytes."""
    library, ltfs = _setup(num_drives=1, barcodes=["SHARD1L8"])
    catalog = _catalog()
    root = _same_named_tree(tmp_path)
    job = catalog.create_job("archive", {"source_path": str(root)})
    run_sharded_archive(
        ShardedArchiveRequest(
            source_path=root,
            volume_group_name="collide",
            lane_barcodes=["SHARD1L8"],
            mode=ShardMode.STRIPE,
        ),
        library,
        ltfs,
        catalog,
        DriveScheduler(num_drives=1),
        job.id,
    )

    destination = tmp_path / "out"
    destination.mkdir()
    for sub in ("alpha", "beta"):
        source = root / sub / "same.txt"
        restore_job = catalog.create_job("restore", {"catalog_path": str(source)})
        run_sharded_restore(
            ShardedRestoreRequest(catalog_path=str(source), dest_path=destination / f"{sub}.txt"),
            library,
            ltfs,
            catalog,
            DriveScheduler(num_drives=1),
            restore_job.id,
        )
        assert (destination / f"{sub}.txt").read_bytes() == source.read_bytes(), (
            f"{sub}/same.txt restored to the wrong file's bytes"
        )


def test_stripe_handles_a_single_file_source(tmp_path: Path) -> None:
    """source_path == the file itself: relative_to() would raise."""
    library, ltfs = _setup(num_drives=1, barcodes=["SHARD1L8"])
    catalog = _catalog()
    only = tmp_path / "solo.bin"
    only.write_bytes(b"solo" * 64)
    job = catalog.create_job("archive", {"source_path": str(only)})

    result = run_sharded_archive(
        ShardedArchiveRequest(
            source_path=only,
            volume_group_name="solo",
            lane_barcodes=["SHARD1L8"],
            mode=ShardMode.STRIPE,
        ),
        library,
        ltfs,
        catalog,
        DriveScheduler(num_drives=1),
        job.id,
    )

    assert result.errors == []
    assert result.files_archived == 1


def test_failed_sharded_archive_records_why_on_the_job(tmp_path: Path) -> None:
    """`failed_recoverable` with `error: null` told the operator nothing."""
    library, ltfs = _setup(num_drives=1, barcodes=["SHARD1L8"])
    catalog = _catalog()
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.bin").write_bytes(b"x" * 1024)
    job = catalog.create_job("archive", {"source_path": str(root)})

    original_write = ltfs.write_file

    def _boom(*args, **kwargs):
        # A TYPED error: its curated message must reach the job record.
        # (A raw RuntimeError would be reduced to its class name by
        # safe_job_error() — jobs.error is served unauthenticated — which the
        # second assertion below pins.)
        from openblade.domain.errors import OpenBladeError

        class LaneWriteError(OpenBladeError):
            pass

        raise LaneWriteError("simulated lane write failure")

    ltfs.write_file = _boom  # type: ignore[method-assign]
    try:
        result = run_sharded_archive(
            ShardedArchiveRequest(
                source_path=root,
                volume_group_name="boom",
                lane_barcodes=["SHARD1L8"],
                mode=ShardMode.STRIPE,
            ),
            library,
            ltfs,
            catalog,
            DriveScheduler(num_drives=1),
            job.id,
        )
    finally:
        ltfs.write_file = original_write  # type: ignore[method-assign]

    assert result.errors
    stored = catalog.get_job(job.id)
    assert stored is not None
    assert stored.state == "failed_recoverable"
    assert stored.error, "the job carried no error at all"
    assert "simulated lane write failure" in stored.error
    # And the leak direction: raw (non-OpenBlade) exception text must NOT
    # appear verbatim — the sanitizer reduces it to the class name.
    from openblade.domain.errors import safe_job_error

    assert "sneaky /dev path" not in safe_job_error(RuntimeError("sneaky /dev path"))


def test_stripe_tape_path_cannot_escape_the_stripe_prefix() -> None:
    """`source_path` comes from the API body and pathlib does not normalise.

    `/data/../etc/passwd` relative to `/data` yields `../etc/passwd`, which
    write_file would join onto the LTFS mount point and escape it.
    """
    from openblade.jobs.sharded_archive import _stripe_tape_path

    escapes = [
        (Path("/data/../etc/passwd"), Path("/data")),
        (Path("/data/a/../../etc/shadow"), Path("/data")),
        (Path("/other/x.bin"), Path("/data")),
    ]
    for source_file, root in escapes:
        result = _stripe_tape_path(source_file, root)
        assert result.startswith("/stripe/"), result
        assert ".." not in result.split("/"), result


def test_stripe_tape_path_of_a_single_file_source_is_not_a_bare_directory() -> None:
    """relative_to(itself) returns Path('.'), which collapsed the path to /stripe."""
    from openblade.jobs.sharded_archive import _stripe_tape_path

    only = Path("/data/solo.bin")
    assert _stripe_tape_path(only, only) == "/stripe/solo.bin"


def test_stripe_tape_path_preserves_unicode_and_collapses_repeated_separators() -> None:
    from openblade.jobs.sharded_archive import _stripe_tape_path

    assert (
        _stripe_tape_path(Path("/data/日本語/記録 1.txt"), Path("/data"))
        == "/stripe/日本語/記録 1.txt"
    )
    assert _stripe_tape_path(Path("/data//a///b.txt"), Path("/data")) == "/stripe/a/b.txt"
