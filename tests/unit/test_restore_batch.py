"""run_restore_batch: one lease/load/mount/unmount/unload per source tape."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.jobs.restore import RestoreRequest, run_restore_batch
from openblade.jobs.scheduler import CatalogLeaseStore, DriveScheduler
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges


class _Recorder:
    """Counts library load/unload calls without changing their behaviour."""

    def __init__(self, library: MockLibraryBackend) -> None:
        self.loads = 0
        self.unloads = 0
        real_load, real_unload = library.load, library.unload

        def load(*args: Any, **kwargs: Any) -> Any:
            self.loads += 1
            return real_load(*args, **kwargs)

        def unload(*args: Any, **kwargs: Any) -> Any:
            self.unloads += 1
            return real_unload(*args, **kwargs)

        library.load = load  # type: ignore[method-assign]
        library.unload = unload  # type: ignore[method-assign]


def _stack(
    tmp_path: Path, groups: list[str]
) -> tuple[CatalogRepository, MockLibraryBackend, MockLTFSBackend]:
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    repo = CatalogRepository(get_session())
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    slots = library.inventory().slots
    for index, name in enumerate(groups):
        slot = slots[index]
        barcode = str(slot.barcode)
        library.load(slot.slot_id, 0)
        ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
        library.unload(0, slot.slot_id)
        group = repo.create_volume_group(name)
        repo.add_barcode_to_volume_group(group.id, barcode)
    return repo, library, ltfs


def _archive(
    repo: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    source: Path,
    group: str,
) -> None:
    job = repo.create_job("archive", {"source_path": str(source), "volume_group": group})
    run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name=group),
        library,
        ltfs,
        repo,
        job.id,
        scheduler=DriveScheduler(1, store=CatalogLeaseStore(repo), job_id=job.id),
    )


def _write(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"payload of {name}")
    return path


def _run(
    repo: CatalogRepository,
    library: MockLibraryBackend,
    ltfs: MockLTFSBackend,
    requests: list[RestoreRequest],
) -> tuple[str, Any]:
    job = repo.create_job("restore", {})
    scheduler = DriveScheduler(1, store=CatalogLeaseStore(repo), job_id=job.id)
    result = run_restore_batch(requests, library, ltfs, repo, job.id, scheduler=scheduler)
    return job.id, result


def test_restore_batch_two_files_one_tape_loads_and_unloads_once(tmp_path: Path) -> None:
    repo, library, ltfs = _stack(tmp_path, ["photos"])
    for name in ("a.txt", "b.txt"):
        _archive(repo, library, ltfs, _write(tmp_path, name), "photos")
    out = tmp_path / "out"
    out.mkdir()
    recorder = _Recorder(library)

    job_id, result = _run(
        repo,
        library,
        ltfs,
        [RestoreRequest(f"/photos/{n}", out / n) for n in ("a.txt", "b.txt")],
    )

    assert (recorder.loads, recorder.unloads) == (1, 1)
    assert result.ok and [i.ok for i in result.items] == [True, True]
    assert (out / "a.txt").read_text() == "payload of a.txt"
    assert (out / "b.txt").read_text() == "payload of b.txt"
    assert repo.get_job(job_id).state == "completed"  # type: ignore[union-attr]


def test_restore_batch_two_tapes_loads_and_unloads_each_once(tmp_path: Path) -> None:
    repo, library, ltfs = _stack(tmp_path, ["photos", "video"])
    _archive(repo, library, ltfs, _write(tmp_path, "a.txt"), "photos")
    _archive(repo, library, ltfs, _write(tmp_path, "c.txt"), "video")
    out = tmp_path / "out"
    out.mkdir()
    recorder = _Recorder(library)

    job_id, result = _run(
        repo,
        library,
        ltfs,
        [
            RestoreRequest("/photos/a.txt", out / "a.txt"),
            RestoreRequest("/video/c.txt", out / "c.txt"),
        ],
    )

    assert (recorder.loads, recorder.unloads) == (2, 2)
    assert result.ok
    events = [e.event for e in repo.job_journal(job_id)]
    assert events.count("batch_tape_started") == 2
    assert events.count("batch_tape_finished") == 2


def test_restore_batch_unknown_path_is_item_failure_not_abort(tmp_path: Path) -> None:
    repo, library, ltfs = _stack(tmp_path, ["photos"])
    _archive(repo, library, ltfs, _write(tmp_path, "a.txt"), "photos")
    out = tmp_path / "out"
    out.mkdir()

    job_id, result = _run(
        repo,
        library,
        ltfs,
        [
            RestoreRequest("/photos/missing.txt", out / "missing.txt"),
            RestoreRequest("/photos/a.txt", out / "a.txt"),
        ],
    )

    by_path = {i.catalog_path: i for i in result.items}
    assert not by_path["/photos/missing.txt"].ok
    assert by_path["/photos/a.txt"].ok and (out / "a.txt").exists()
    assert repo.get_job(job_id).state == "failed"  # type: ignore[union-attr]


def test_restore_batch_unmount_failure_skips_unload_and_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, library, ltfs = _stack(tmp_path, ["photos"])
    _archive(repo, library, ltfs, _write(tmp_path, "a.txt"), "photos")
    out = tmp_path / "out"
    out.mkdir()
    recorder = _Recorder(library)

    def broken_unmount(handle: Any) -> Any:
        raise OSError("simulated unmount failure")

    monkeypatch.setattr(ltfs, "unmount", broken_unmount)

    job_id, result = _run(repo, library, ltfs, [RestoreRequest("/photos/a.txt", out / "a.txt")])

    assert recorder.loads == 1
    assert recorder.unloads == 0  # never unload a (maybe) still-mounted tape
    assert not result.ok
    assert "physical_state_unknown" in [e.event for e in repo.job_journal(job_id)]
    assert repo.get_job(job_id).state == "failed"  # type: ignore[union-attr]
