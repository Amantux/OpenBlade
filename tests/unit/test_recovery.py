from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from openblade.bootstrap import create_context
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.jobs.recovery import INTERRUPTED_ERROR, recover_after_restart
from openblade.simulator.library import MockLibraryBackend


def _lease(catalog: CatalogRepository, job_id: str, barcode: str, *, ttl: timedelta) -> str:
    leases = catalog.acquire_drive_leases(job_id=job_id, barcodes=[barcode], num_drives=2, ttl=ttl)
    assert leases is not None
    return leases[0].id


def test_recovery_fails_only_jobs_whose_lease_expired(tmp_path: Path) -> None:
    # Two `running` jobs share the catalog. One stopped heartbeating (dead
    # process); the other holds a live lease — it may be running in ANOTHER
    # process right now, and recovery must not touch it.
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    catalog = context.catalog
    dead = catalog.create_job("archive", {})
    catalog.update_job_state(dead.id, "running")
    dead_lease = _lease(catalog, dead.id, "MCK00001", ttl=timedelta(seconds=-1))
    alive = catalog.create_job("archive", {})
    catalog.update_job_state(alive.id, "running")
    alive_lease = _lease(catalog, alive.id, "MCK00002", ttl=timedelta(minutes=15))
    empty_library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)

    report = recover_after_restart(catalog, empty_library)

    assert report.interrupted_job_ids == [dead.id]
    assert report.released_lease_ids == [dead_lease]
    refreshed = catalog.get_job(dead.id)
    assert refreshed is not None
    assert refreshed.state == "failed_recoverable"
    assert refreshed.error == INTERRUPTED_ERROR
    assert [(m.expected_barcode, m.observed_barcode) for m in report.mismatches] == [
        ("MCK00001", None)
    ]
    untouched = catalog.get_job(alive.id)
    assert untouched is not None
    assert untouched.state == "running"
    assert [lease.id for lease in catalog.live_leases()] == [alive_lease]


def test_recovery_releases_live_lease_of_a_finished_job(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    catalog = context.catalog
    done = catalog.create_job("archive", {})
    catalog.update_job_state(done.id, "completed")
    lease_id = _lease(catalog, done.id, "MCK00001", ttl=timedelta(minutes=15))
    library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)

    report = recover_after_restart(catalog, library)

    assert report.interrupted_job_ids == []
    assert report.released_lease_ids == [lease_id]
    assert catalog.live_leases() == []


def test_restart_runs_recovery_and_exposes_it_read_only(tmp_path: Path) -> None:
    # A job left `running` by a previous process is failed on the next start,
    # and the report is visible on the native surface only.
    from fastapi.testclient import TestClient

    from openblade.api.main import app
    from openblade.bootstrap import reset_context

    db_url = f"sqlite:///{tmp_path / 'restart.db'}"
    first = create_context(OpenBladeConfig(db_url=db_url))
    job = first.catalog.create_job("archive", {})
    first.catalog.update_job_state(job.id, "running")
    _lease(first.catalog, job.id, "MCK00001", ttl=timedelta(seconds=-1))

    second = create_context(OpenBladeConfig(db_url=db_url))
    reset_context(second)
    assert second.recovery_report.interrupted_job_ids == [job.id]

    response = TestClient(app).get("/jobs/recovery")
    assert response.status_code == 200
    assert response.json()["interrupted_job_ids"] == [job.id]

    strict = create_context(OpenBladeConfig(db_url=db_url, scalar_api_only=True))
    reset_context(strict)
    assert TestClient(app).get("/jobs/recovery").status_code == 404
