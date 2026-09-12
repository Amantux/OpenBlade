from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.capacity import capacity_reserve_bytes, has_room_for
from openblade.domain.errors import (
    BarcodeMismatchError,
    CartridgeOfflineError,
    ChecksumMismatchError,
)
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.jobs.format import run_format_job
from openblade.jobs.restore import RestoreRequest, run_restore_job
from openblade.jobs.verify import sha256sum
from openblade.simulator.faults import FaultConfig, FaultType
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges


def make_catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def _formatted_stack() -> tuple[CatalogRepository, MockLibraryBackend, MockLTFSBackend, str]:
    catalog = make_catalog()
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    library.unload(0, 1)
    group = catalog.create_volume_group("photos")
    catalog.add_barcode_to_volume_group(group.id, barcode)
    return catalog, library, ltfs, barcode


def test_archive_job_full_cycle(tmp_path: Path) -> None:
    catalog, library, ltfs, _ = _formatted_stack()
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_text("hello archive")
    job = catalog.create_job("archive", {"source_path": str(source), "volume_group": "photos"})

    result = run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        catalog,
        job.id,
    )

    record = catalog.get_file_record("/photos/a.txt")
    assert result.files_archived == 1
    assert record is not None
    assert record.checksum_sha256 == sha256sum(original)
    assert record.instances[-1].state == "archived"


def test_archive_job_checksum_mismatch_leaves_pending(tmp_path: Path) -> None:
    catalog = make_catalog()
    library, _ = one_drive_twenty_slots_five_cartridges()
    faulty = MockLTFSBackend(
        library, fault_config=FaultConfig.with_fault(FaultType.CHECKSUM_MISMATCH)
    )
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    faulty.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    library.unload(0, 1)
    group = catalog.create_volume_group("photos")
    catalog.add_barcode_to_volume_group(group.id, barcode)
    source = tmp_path / "source"
    source.mkdir()
    original = source / "bad.txt"
    original.write_text("checksum fail")
    job = catalog.create_job("archive", {"source_path": str(source), "volume_group": "photos"})

    with pytest.raises(ChecksumMismatchError):
        run_archive_job(
            ArchiveRequest(source_path=source, volume_group_name="photos"),
            library,
            faulty,
            catalog,
            job.id,
        )

    record = catalog.get_file_record("/photos/bad.txt")
    assert record is not None
    assert record.instances[-1].state == "pending"


def test_restore_job_full_cycle(tmp_path: Path) -> None:
    catalog, library, ltfs, _ = _formatted_stack()
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_text("hello restore")
    archive_job = catalog.create_job(
        "archive", {"source_path": str(source), "volume_group": "photos"}
    )
    run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        catalog,
        archive_job.id,
    )
    restore_job = catalog.create_job("restore", {"catalog_path": "/photos/a.txt"})
    destination = tmp_path / "restore"
    destination.mkdir()

    result = run_restore_job(
        RestoreRequest(catalog_path="/photos/a.txt", dest_path=destination),
        library,
        ltfs,
        catalog,
        restore_job.id,
    )

    restored = destination / "a.txt"
    assert result.checksum_verified is True
    assert sha256sum(restored) == sha256sum(original)


def test_restore_offline_cartridge_raises(tmp_path: Path) -> None:
    catalog, library, ltfs, barcode = _formatted_stack()
    source = tmp_path / "source"
    source.mkdir()
    original = source / "a.txt"
    original.write_text("offline")
    archive_job = catalog.create_job(
        "archive", {"source_path": str(source), "volume_group": "photos"}
    )
    run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        catalog,
        archive_job.id,
    )
    cartridge = catalog.get_cartridge(barcode)
    assert cartridge is not None
    cartridge.state = "exported"
    catalog.session.commit()
    library.export_cartridge(barcode)
    restore_job = catalog.create_job("restore", {"catalog_path": "/photos/a.txt"})

    with pytest.raises(CartridgeOfflineError):
        run_restore_job(
            RestoreRequest(catalog_path="/photos/a.txt", dest_path=tmp_path / "restored.txt"),
            library,
            ltfs,
            catalog,
            restore_job.id,
        )


def test_format_job_wrong_barcode_raises() -> None:
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    with pytest.raises(BarcodeMismatchError):
        run_format_job(
            barcode,
            FormatConfirmation("WRONG001", SafetyToken.generate("format", barcode)),
            library,
            ltfs,
        )


# --- Real-data campaign regression -------------------------------------------
# docs/runbooks/real-data-campaign.md: archiving 430 MB of real files onto small
# tapes died with OSError [Errno 28] on the *empty* files. `_choose_tape` asked
# `remaining_capacity >= size_bytes`, which is True for `0 >= 0`, so every
# zero-byte file was routed back to the already-full first tape -- and an empty
# file still needs a directory entry and index space, so LTFS said ENOSPC and the
# whole job died after 617 of 1,073 files.


def test_empty_file_is_not_routed_to_a_full_tape(tmp_path: Path) -> None:
    from openblade.jobs.archive import _choose_tape

    catalog, library, ltfs, full_barcode = _formatted_stack()
    group = catalog.get_volume_group("photos")
    assert group is not None

    # Fill the group's only tape completely.
    tape = ltfs.ensure_tape(full_barcode)
    tape.used_bytes = tape.capacity_bytes

    # A second, blank tape is available in the library inventory.
    spare = str(library.inventory().slots[1].barcode)

    chosen = _choose_tape(catalog, library, ltfs, group.id, 0)

    assert chosen != full_barcode, "a zero-byte file was routed onto a full tape"
    assert chosen == spare


def test_full_tape_is_rejected_for_a_sized_file_too(tmp_path: Path) -> None:
    from openblade.jobs.archive import _choose_tape

    catalog, library, ltfs, full_barcode = _formatted_stack()
    group = catalog.get_volume_group("photos")
    assert group is not None
    tape = ltfs.ensure_tape(full_barcode)
    tape.used_bytes = tape.capacity_bytes

    assert _choose_tape(catalog, library, ltfs, group.id, 1024) != full_barcode


def test_a_tape_with_exactly_enough_room_is_still_chosen(tmp_path: Path) -> None:
    """The fix must not become an off-by-one that rejects a perfect fit.

    "Enough room" now means enough room *above the LTFS-index reserve* -- the
    review of the campaign pointed out that "zero bytes free" was the wrong
    threshold, because the overhead that made empty files ENOSPC does not
    disappear at one byte. See openblade/domain/capacity.py.
    """
    from openblade.jobs.archive import _choose_tape

    catalog, library, ltfs, barcode = _formatted_stack()
    group = catalog.get_volume_group("photos")
    assert group is not None
    tape = ltfs.ensure_tape(barcode)
    tape.used_bytes = tape.capacity_bytes - capacity_reserve_bytes(tape.capacity_bytes) - 4096

    assert _choose_tape(catalog, library, ltfs, group.id, 4096) == barcode


# --- The reserve (campaign review: "Reported rather than changed") ------------
# "`_has_room_for` only rejects a tape with *exactly* zero bytes free. The stated
# cause is LTFS index overhead, so the threshold should be a reserve, not 1 byte;
# a tape with 512 bytes left still ENOSPCs on an empty file."


def test_a_nearly_full_tape_does_not_take_an_empty_file(tmp_path: Path) -> None:
    """A sliver of free space is still full -- the case the old guard let through.

    The runbook's example is "512 bytes left" on a 6.57 GB cartridge; the
    simulator's scenario tapes are 4 KiB, so the same shape here is a sliver
    smaller than that tape's reserve.
    """
    from openblade.jobs.archive import _choose_tape

    catalog, library, ltfs, nearly_full = _formatted_stack()
    group = catalog.get_volume_group("photos")
    assert group is not None
    tape = ltfs.ensure_tape(nearly_full)
    sliver = max(1, capacity_reserve_bytes(tape.capacity_bytes) // 2)
    tape.used_bytes = tape.capacity_bytes - sliver
    assert ltfs.remaining_capacity(nearly_full) > 0, "not the old zero-bytes-free case"

    spare = str(library.inventory().slots[1].barcode)
    assert _choose_tape(catalog, library, ltfs, group.id, 0) == spare


def test_spillover_triggers_at_the_reserve_not_at_zero(tmp_path: Path) -> None:
    """The boundary, from both sides, through the real selection path."""
    from openblade.jobs.archive import _choose_tape

    catalog, library, ltfs, barcode = _formatted_stack()
    group = catalog.get_volume_group("photos")
    assert group is not None
    tape = ltfs.ensure_tape(barcode)
    capacity = tape.capacity_bytes
    reserve = capacity_reserve_bytes(capacity)
    spare = str(library.inventory().slots[1].barcode)

    # free == reserve -> full, even for a zero-byte file.
    tape.used_bytes = capacity - reserve
    assert _choose_tape(catalog, library, ltfs, group.id, 0) == spare

    # free == reserve + 1 -> one byte of usable room, so it is chosen again.
    tape.used_bytes = capacity - reserve - 1
    assert _choose_tape(catalog, library, ltfs, group.id, 0) == barcode
    assert _choose_tape(catalog, library, ltfs, group.id, 1) == barcode
    assert _choose_tape(catalog, library, ltfs, group.id, 2) == spare


def test_archive_job_spills_from_a_nearly_full_tape(tmp_path: Path) -> None:
    """End-to-end: the rig shape, but with the tape merely *nearly* full."""
    catalog, library, ltfs, first = _formatted_stack()
    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    (source / "logs" / "empty.log").write_bytes(b"")
    (source / "payload.bin").write_bytes(b"z" * 1024)

    tape = ltfs.ensure_tape(first)
    tape.used_bytes = tape.capacity_bytes - max(1, capacity_reserve_bytes(tape.capacity_bytes) // 2)
    assert ltfs.remaining_capacity(first) > 0, "not the old zero-bytes-free case"
    spare = str(library.inventory().slots[1].barcode)
    library.load(2, 0)
    ltfs.format(spare, FormatConfirmation(spare, SafetyToken.generate("format", spare)))
    library.unload(0, 2)

    job = catalog.create_job("archive", {"source_path": str(source), "volume_group": "photos"})
    result = run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        catalog,
        job.id,
    )

    assert result.errors == []
    assert result.files_archived == 2
    assert first not in result.tapes_used


def test_real_backend_hydrates_its_capacities_from_the_catalog(tmp_path: Path) -> None:
    """Round trip: catalog row -> bootstrap -> RealLTFSBackend._tapes.

    The campaign left this as "a tape this process has never mounted still
    assumes 12 GB", which makes the first spill decision after every restart
    wrong. The catalog is the authority because jobs/archive.py writes these
    columns from the statvfs measurement taken while the tape was mounted.
    """
    import dataclasses

    from openblade.bootstrap import _catalog_tape_states
    from openblade.config import OpenBladeConfig
    from openblade.domain.policies import RealHardwareGuard
    from openblade.hardware.ltfs import RealLTFSBackend
    from openblade.hardware.runner import SafeRunner

    db_path = tmp_path / "catalog.db"
    db_url = f"sqlite:///{db_path}"
    init_db(db_url)
    catalog = CatalogRepository(get_session())
    group = catalog.create_volume_group("photos")
    measured = catalog.add_cartridge("OB0001L8", group.id)
    measured.capacity_bytes = 6_569_328_640  # what the rig actually reports
    measured.used_bytes = 6_569_328_128  # 512 bytes free: the runbook's ENOSPC case
    catalog.session.commit()

    # A fresh process: nothing has been mounted, `_tapes` starts empty.
    states = _catalog_tape_states(dataclasses.replace(OpenBladeConfig(), db_url=db_url))
    backend = RealLTFSBackend(
        library=object(),  # type: ignore[arg-type]  -- unused on this path
        guard=RealHardwareGuard(
            config_backend="real",
            config_real_hardware_enabled=True,
            operator_acknowledgment="hydration-test",
        ),
        runner=SafeRunner(dry_run=True),
        mount_root=tmp_path / "mnt",
        known_tapes=states,
    )

    tape = backend.ensure_tape("OB0001L8")
    assert tape.capacity_bytes == 6_569_328_640, "hydration did not reach the backend"
    assert tape.capacity_bytes != 12_000_000_000, "still the fictional default"
    assert tape.used_bytes == 6_569_328_128
    # ...and the consequence that matters: it is already known to be full.
    assert has_room_for(tape.capacity_bytes, tape.used_bytes, 0) is False

    # A barcode with no catalog row falls back to the default rather than crashing.
    assert backend.ensure_tape("OB0009L8").capacity_bytes == 12_000_000_000


def test_archive_job_spills_an_empty_file_onto_the_next_tape(tmp_path: Path) -> None:
    """End-to-end: the exact shape that failed on the rig."""
    catalog, library, ltfs, first = _formatted_stack()
    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    (source / "logs" / "empty.log").write_bytes(b"")
    (source / "payload.bin").write_bytes(b"z" * 1024)

    # The first tape is full before the job starts; a second is formatted and blank.
    ltfs.ensure_tape(first).used_bytes = ltfs.ensure_tape(first).capacity_bytes
    spare = str(library.inventory().slots[1].barcode)
    library.load(2, 0)
    ltfs.format(spare, FormatConfirmation(spare, SafetyToken.generate("format", spare)))
    library.unload(0, 2)

    job = catalog.create_job("archive", {"source_path": str(source), "volume_group": "photos"})
    result = run_archive_job(
        ArchiveRequest(source_path=source, volume_group_name="photos"),
        library,
        ltfs,
        catalog,
        job.id,
    )

    assert result.errors == []
    assert result.files_archived == 2
    assert first not in result.tapes_used
