"""Regression: the archive-job completion log must not crash when INFO is on.

`run_archive_job` ended with structlog-style kwargs on a *stdlib* logger
(`logger.info("...", job_id=..., files_archived=...)`), which raises TypeError
inside `Logger._log()`. `Logger.info` checks `isEnabledFor(INFO)` first, so the
call was a no-op in a test suite that never enables INFO -- and blew up on the
SUCCESS path of a real archive job in any deployment that logs at INFO, after
the catalog had already been marked completed.

The test therefore has to raise the level explicitly; running the archive job
without doing so proves nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.domain.policies import FormatConfirmation, SafetyToken
from openblade.jobs.archive import ArchiveRequest, run_archive_job
from openblade.simulator.library import MockLibraryBackend
from openblade.simulator.ltfs_volume import MockLTFSBackend
from openblade.simulator.scenarios import one_drive_twenty_slots_five_cartridges

_ARCHIVE_LOGGER = "openblade.jobs.archive"


def _formatted_stack() -> tuple[CatalogRepository, MockLibraryBackend, MockLTFSBackend]:
    init_db("sqlite:///:memory:")
    catalog = CatalogRepository(get_session())
    library, ltfs = one_drive_twenty_slots_five_cartridges()
    barcode = str(library.inventory().slots[0].barcode)
    library.load(1, 0)
    ltfs.format(barcode, FormatConfirmation(barcode, SafetyToken.generate("format", barcode)))
    library.unload(0, 1)
    group = catalog.create_volume_group("photos")
    catalog.add_barcode_to_volume_group(group.id, barcode)
    return catalog, library, ltfs


def test_archive_job_completion_log_survives_info_level(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalog, library, ltfs = _formatted_stack()
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("hello archive")
    job = catalog.create_job("archive", {"source_path": str(source), "volume_group": "photos"})

    with caplog.at_level(logging.INFO, logger=_ARCHIVE_LOGGER):
        result = run_archive_job(
            ArchiveRequest(source_path=source, volume_group_name="photos"),
            library,
            ltfs,
            catalog,
            job.id,
        )

    assert result.files_archived == 1
    completion = [
        record
        for record in caplog.records
        if record.name == _ARCHIVE_LOGGER and record.message == "archive job completed"
    ]
    assert len(completion) == 1
    # The structured fields survive the fix -- they moved into `extra`, which
    # attaches them to the record rather than being passed to `_log()`.
    assert completion[0].job_id == job.id
    assert completion[0].files_archived == 1
