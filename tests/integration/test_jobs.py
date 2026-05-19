from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
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
