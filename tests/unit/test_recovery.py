from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig
from openblade.jobs.recovery import INTERRUPTED_ERROR, recover_after_restart
from openblade.simulator.library import MockLibraryBackend


def test_recover_after_restart_running_job_marked_failed_recoverable_and_lease_released(
    tmp_path: Path,
) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    catalog = context.catalog
    job = catalog.create_job("archive", {})
    catalog.update_job_state(job.id, "running")
    leases = catalog.acquire_drive_leases(
        job_id=job.id, barcodes=["MCK00001"], num_drives=2, ttl=timedelta(minutes=15)
    )
    assert leases is not None
    empty_library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)

    report = recover_after_restart(catalog, empty_library)

    refreshed = catalog.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "failed_recoverable"
    assert refreshed.error == INTERRUPTED_ERROR
    assert catalog.live_leases() == []
    assert report.released_lease_ids == [leases[0].id]
    assert [(m.expected_barcode, m.observed_barcode) for m in report.mismatches] == [
        ("MCK00001", None)
    ]
