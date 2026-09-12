"""Bulk (tree) restore across a seeded multi-tape catalog.

The behaviours worth defending, all of them recorded as real findings in
docs/runbooks/real-data-campaign.md:

* restore-to-a-directory uses the basename only, which COLLAPSES same-named
  files from different subdirectories. A bulk restore doing that loses data
  silently, so the tree must be preserved.
* ``list_file_records`` matches SQL ``LIKE 'prefix%'``, so a prefix must be
  matched on path components or ``/photo`` drags in ``/photos-old``.
* a file whose shards live on two tapes has to come back whole.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.errors import UnsafeCatalogPathError
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.scheduler import DriveScheduler
from openblade.jobs.shard import ShardMode
from openblade.jobs.sharded_archive import ShardedArchiveRequest, run_sharded_archive
from openblade.jobs.tree_restore import (
    TreeRestoreRequest,
    _relative_parts,
    plan_tree_restore,
    run_tree_restore,
)
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend

BARCODES = ["OB0001L8", "OB0002L8", "OB0003L8"]


@pytest.fixture
def rig(tmp_path: Path):
    """A three-tape simulator with a catalog, all tapes formatted."""
    init_db("sqlite:///:memory:")
    repo = CatalogRepository(get_session())
    library = MockLibraryBackend(num_slots=8, num_drives=3, num_import_export_slots=2)
    library.seed_slots(BARCODES)
    ltfs = MockLTFSBackend(library)
    for barcode in BARCODES:
        ltfs.format(
            barcode,
            FormatConfirmation(
                expected_barcode=barcode,
                safety_token=SafetyToken.generate("format", barcode),
            ),
        )
    return repo, library, ltfs


def seed_source(root: Path) -> dict[str, bytes]:
    """A source tree with the exact shape that breaks a basename-only restore."""
    files = {
        "alpha/same.txt": b"I am alpha",
        "beta/same.txt": b"I am beta",
        "beta/deep/nested.bin": bytes(range(256)) * 40,
        "top.txt": b"top level",
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return files


def archive_tree(rig, source: Path, volume_group: str, mode: ShardMode, lanes: list[str]):
    repo, library, ltfs = rig
    job = repo.create_job("archive", {})
    return run_sharded_archive(
        ShardedArchiveRequest(
            source_path=source,
            volume_group_name=volume_group,
            lane_barcodes=lanes,
            mode=mode,
        ),
        library,
        ltfs,
        repo,
        DriveScheduler(num_drives=3),
        job.id,
    )


class TestRelativeParts:
    @pytest.mark.parametrize(
        ("catalog_path", "prefix", "expected"),
        [
            ("/photos/a/x.txt", "/photos", ("a", "x.txt")),
            ("/photos/x.txt", "/photos", ("x.txt",)),
            ("/photos", "/photos", ("photos",)),
            ("/photos/a/x.txt", "/", ("photos", "a", "x.txt")),
        ],
    )
    def test_maps_catalog_paths_to_destination_components(
        self, catalog_path: str, prefix: str, expected: tuple[str, ...]
    ) -> None:
        assert _relative_parts(catalog_path, prefix) == expected

    @pytest.mark.parametrize(
        "catalog_path",
        [
            "/vg/../../etc/passwd",
            "/vg/a/../../../../etc/shadow",
            "/vg//double///slash.txt",
            "/vg/./dot.txt",
            "/vg/..",
        ],
    )
    def test_no_catalog_path_can_escape_the_destination_directory(
        self, catalog_path: str, tmp_path: Path
    ) -> None:
        """The destination is joined from catalog rows, so treat them as hostile.

        A `..` component surviving into `dest.joinpath(*parts)` writes outside
        `--dest`; an absolute component would reset the join to the filesystem
        root (`Path("/a").joinpath("/etc")` is `/etc`). The invariant is "never
        outside dest", so either outcome is acceptable: a contained path, or a
        typed refusal. What is NOT acceptable is a path that escapes.

        `/vg/..` is the case that caught a real bug: it reduces to no safe
        component, and the old `or (path.name,)` fallback put `..` straight back
        because that is literally the basename.
        """
        dest = tmp_path / "dest"
        try:
            parts = _relative_parts(catalog_path, "/vg")
        except UnsafeCatalogPathError:
            return  # refused, which is the other safe answer

        resolved = dest.joinpath(*parts)
        assert ".." not in resolved.parts
        assert resolved.is_relative_to(dest)

    def test_a_traversal_only_path_is_refused_by_type(self) -> None:
        with pytest.raises(UnsafeCatalogPathError, match="refusing"):
            _relative_parts("/vg/..", "/vg")


class TestPlanning:
    def test_prefix_matching_is_component_aware(self, rig, tmp_path: Path) -> None:
        """`/photo` must NOT pull in `/photos-old/...` -- SQL LIKE would."""
        repo, _, _ = rig
        group = repo.create_volume_group("g")
        for path in ("/photo/keep.txt", "/photos-old/skip.txt", "/photo/sub/keep2.txt"):
            record = repo.create_file_record(path, 10, "abc", group.id)
            instance = repo.create_file_instance(record.id, BARCODES[0], path)
            repo.mark_instance_archived(instance.id)

        planned, _ = plan_tree_restore(
            TreeRestoreRequest(catalog_prefix="/photo", dest_dir=tmp_path), repo
        )

        assert [item.catalog_path for item in planned] == [
            "/photo/keep.txt",
            "/photo/sub/keep2.txt",
        ]

    def test_plan_is_grouped_by_cartridge(self, rig, tmp_path: Path) -> None:
        repo, _, _ = rig
        group = repo.create_volume_group("g")
        seeded = [
            ("/g/a.txt", BARCODES[1]),
            ("/g/b.txt", BARCODES[0]),
            ("/g/c.txt", BARCODES[1]),
            ("/g/d.txt", BARCODES[0]),
        ]
        for path, barcode in seeded:
            record = repo.create_file_record(path, 10, "abc", group.id)
            instance = repo.create_file_instance(record.id, barcode, path)
            repo.mark_instance_archived(instance.id)

        planned, _ = plan_tree_restore(
            TreeRestoreRequest(catalog_prefix="/g", dest_dir=tmp_path), repo
        )

        assert [item.barcode for item in planned] == [
            BARCODES[0],
            BARCODES[0],
            BARCODES[1],
            BARCODES[1],
        ]
        # Deterministic within a tape, so a re-run restores in the same order.
        assert [item.catalog_path for item in planned][:2] == ["/g/b.txt", "/g/d.txt"]

    def test_a_record_with_no_archived_instance_is_reported_as_skipped(
        self, rig, tmp_path: Path
    ) -> None:
        """Not restorable, not a failure -- but never silent.

        A failed archive leaves exactly this shape. An operator who asks for a
        tree and gets fewer files than the catalog lists must be told which,
        or "completed" is a silent wrong result.
        """
        repo, _, _ = rig
        group = repo.create_volume_group("g")
        record = repo.create_file_record("/g/pending.txt", 10, "abc", group.id)
        repo.create_file_instance(record.id, BARCODES[0], "/g/pending.txt")  # pending

        planned, skipped = plan_tree_restore(
            TreeRestoreRequest(catalog_prefix="/g", dest_dir=tmp_path), repo
        )

        assert planned == []
        assert skipped == ["/g/pending.txt"]

    def test_the_summary_names_skipped_records(self, rig, tmp_path: Path) -> None:
        repo, library, ltfs = rig
        group = repo.create_volume_group("g")
        record = repo.create_file_record("/g/pending.txt", 10, "abc", group.id)
        repo.create_file_instance(record.id, BARCODES[0], "/g/pending.txt")

        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix="/g", dest_dir=tmp_path / "out"),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=1),
            job.id,
        )

        assert result.skipped == ["/g/pending.txt"]
        assert result.to_dict()["filesSkipped"] == 1
        assert result.to_dict()["skippedPaths"] == ["/g/pending.txt"]


class TestStripeAcrossTapes:
    def test_restores_the_whole_tree_byte_for_byte(self, rig, tmp_path: Path) -> None:
        repo, library, ltfs = rig
        source = tmp_path / "src"
        expected = seed_source(source)
        archive_tree(rig, source, "photos", ShardMode.STRIPE, BARCODES[:3])
        dest = tmp_path / "out"

        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix=str(source), dest_dir=dest),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert result.ok
        assert result.files_restored == len(expected)
        assert result.files_failed == 0
        for relative, payload in expected.items():
            assert (dest / relative).read_bytes() == payload

    def test_same_named_files_in_different_directories_do_not_collide(
        self, rig, tmp_path: Path
    ) -> None:
        """The runbook's basename-collapse defect, made impossible here."""
        repo, library, ltfs = rig
        source = tmp_path / "src"
        seed_source(source)
        archive_tree(rig, source, "photos", ShardMode.STRIPE, BARCODES[:3])
        dest = tmp_path / "out"

        job = repo.create_job("restore", {})
        run_tree_restore(
            TreeRestoreRequest(catalog_prefix=str(source), dest_dir=dest),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert (dest / "alpha/same.txt").read_bytes() == b"I am alpha"
        assert (dest / "beta/same.txt").read_bytes() == b"I am beta"

    def test_summary_counts_files_per_tape_and_spans_more_than_one(
        self, rig, tmp_path: Path
    ) -> None:
        repo, library, ltfs = rig
        source = tmp_path / "src"
        seed_source(source)
        archive_tree(rig, source, "photos", ShardMode.STRIPE, BARCODES[:3])

        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix=str(source), dest_dir=tmp_path / "out"),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert len(result.per_tape_counts) > 1, "4 files over 3 lanes must span tapes"
        assert sum(result.per_tape_counts.values()) == result.files_restored
        assert result.files_verified == result.files_restored
        assert set(result.per_tape_counts).issubset(set(BARCODES))

    def test_progress_is_reported_per_file(self, rig, tmp_path: Path) -> None:
        repo, library, ltfs = rig
        source = tmp_path / "src"
        expected = seed_source(source)
        archive_tree(rig, source, "photos", ShardMode.STRIPE, BARCODES[:3])
        ticks = []

        job = repo.create_job("restore", {})
        run_tree_restore(
            TreeRestoreRequest(catalog_prefix=str(source), dest_dir=tmp_path / "out"),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
            progress=ticks.append,
        )

        assert len(ticks) == len(expected)
        assert [tick.files_done for tick in ticks] == list(range(1, len(expected) + 1))
        assert all(tick.files_total == len(expected) for tick in ticks)
        assert ticks[-1].bytes_done > 0


class TestBlockStripeSpanningTapes:
    def test_a_file_split_across_tapes_comes_back_whole(
        self, rig, tmp_path: Path
    ) -> None:
        repo, library, ltfs = rig
        source = tmp_path / "src"
        source.mkdir()
        payload = bytes(range(256)) * 5000
        (source / "big.bin").write_bytes(payload)
        archive_tree(rig, source, "shards", ShardMode.BLOCK_STRIPE, BARCODES[:3])

        record = repo.get_file_record(str(source / "big.bin"))
        assert len(repo.list_shard_records(record.id)) >= 2, "must actually be sharded"

        dest = tmp_path / "out"
        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix=str(source), dest_dir=dest),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert result.ok
        assert (dest / "big.bin").read_bytes() == payload
        # The per-tape counts name every cartridge the file actually came off.
        assert len(result.per_tape_counts) >= 2


class TestFailures:
    def test_one_offline_cartridge_fails_that_file_and_the_job_not_the_run(
        self, rig, tmp_path: Path
    ) -> None:
        repo, library, ltfs = rig
        group = repo.create_volume_group("g")
        for path, barcode in (("/g/ok.txt", BARCODES[0]), ("/g/gone.txt", BARCODES[1])):
            repo.add_cartridge(barcode, group.id)
            record = repo.create_file_record(path, 4, "abc", group.id)
            instance = repo.create_file_instance(record.id, barcode, path)
            repo.mark_instance_archived(instance.id)
        # Write real bytes for the reachable one so it can actually restore.
        _write_through_ltfs(library, ltfs, BARCODES[0], "/g/ok.txt", b"okay", repo)
        repo.set_cartridge_state(BARCODES[1], "exported")

        dest = tmp_path / "out"
        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix="/g", dest_dir=dest),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert result.ok is False
        assert result.files_failed == 1
        assert [failure.catalog_path for failure in result.failures] == ["/g/gone.txt"]
        assert "offline" in result.failures[0].error
        # The run did not abort: the reachable file still came back.
        assert result.files_restored == 1
        assert repo.get_job(job.id).state == "failed"

    def test_failures_carry_a_curated_message_not_raw_exception_text(
        self, rig, tmp_path: Path
    ) -> None:
        """jobs.error is an unauthenticated surface; safe_job_error owns it."""
        repo, library, ltfs = rig
        group = repo.create_volume_group("g")
        record = repo.create_file_record("/g/x.txt", 4, "abc", group.id)
        instance = repo.create_file_instance(record.id, BARCODES[0], "/g/x.txt")
        repo.mark_instance_archived(instance.id)

        class Exploding:
            def __getattr__(self, name):
                raise OSError("SECRET /dev/nst0 device detail")

        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(catalog_prefix="/g", dest_dir=tmp_path / "out"),
            Exploding(),
            ltfs,
            repo,
            DriveScheduler(num_drives=1),
            job.id,
        )

        assert result.files_failed == 1
        assert "SECRET" not in result.failures[0].error
        assert "OSError" in result.failures[0].error


class TestDryRun:
    def test_plans_without_moving_media(self, rig, tmp_path: Path) -> None:
        repo, library, ltfs = rig
        source = tmp_path / "src"
        expected = seed_source(source)
        archive_tree(rig, source, "photos", ShardMode.STRIPE, BARCODES[:3])
        dest = tmp_path / "out"
        ops_before = len(repo.list_tape_ops(limit=1000))

        job = repo.create_job("restore", {})
        result = run_tree_restore(
            TreeRestoreRequest(
                catalog_prefix=str(source), dest_dir=dest, dry_run=True
            ),
            library,
            ltfs,
            repo,
            DriveScheduler(num_drives=3),
            job.id,
        )

        assert result.files_restored == len(expected)
        assert result.dry_run is True
        assert not dest.exists()
        assert len(repo.list_tape_ops(limit=1000)) == ops_before


def _write_through_ltfs(library, ltfs, barcode, tape_path, payload, repo) -> None:
    from openblade.domain.models import MountMode

    slot = library.find_slot_by_barcode(barcode)
    library.load(slot, 0)
    handle = ltfs.mount(barcode, MountMode.READ_WRITE)
    ltfs.write_bytes(handle, PurePosixPath(tape_path), payload)
    ltfs.unmount(handle)
    library.unload(0, slot)
    record = repo.get_file_record(tape_path)
    import hashlib

    record.checksum_sha256 = hashlib.sha256(payload).hexdigest()
    record.size_bytes = len(payload)
    repo.session.commit()
