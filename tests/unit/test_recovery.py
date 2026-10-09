from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from openblade.bootstrap import create_context
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.jobs.recovery import INTERRUPTED_ERROR, recover_after_restart
from openblade.jobs.scheduler import DEFAULT_LEASE_TTL
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
    body = response.json()
    assert body["interrupted_job_ids"] == [job.id]
    assert body["staged_instances"] == {job.id: []}
    assert body["stale_pending_job_ids"] == []

    strict = create_context(OpenBladeConfig(db_url=db_url, scalar_api_only=True))
    reset_context(strict)
    assert TestClient(app).get("/jobs/recovery").status_code == 404


def test_recovery_lists_staged_instances_of_an_interrupted_job(tmp_path: Path) -> None:
    # A sharded archive died after staging shards but before the commit marker:
    # the operator needs to know exactly which tape paths are uncommitted.
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    catalog = context.catalog
    job = catalog.create_job("archive", {})
    catalog.update_job_state(job.id, "running")
    _lease(catalog, job.id, "AAA001L9", ttl=timedelta(seconds=-1))
    vg = catalog.create_volume_group("vg-staged")
    record = catalog.create_file_record("/data/big.bin.shard0", 10, "0" * 64, vg.id, shard_index=0)
    staged = catalog.create_staged_instance(job.id, record.id, "AAA001L9", "/big.bin.shard0")
    library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)

    report = recover_after_restart(catalog, library)

    assert report.interrupted_job_ids == [job.id]
    [listed] = report.staged_instances[job.id]
    assert (listed.instance_id, listed.barcode, listed.tape_path, listed.shard_index) == (
        staged.id,
        "AAA001L9",
        "/big.bin.shard0",
        0,
    )
    assert listed.state == "staging"
    assert "recovered" in [entry.event for entry in catalog.job_journal(job.id)]


def test_recovery_reports_stale_pending_jobs_without_touching_them(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    catalog = context.catalog
    stale = catalog.create_job("archive", {})
    fresh = catalog.create_job("archive", {})
    old_but_leased = catalog.create_job("archive", {})
    long_ago = datetime.now(UTC).replace(tzinfo=None) - DEFAULT_LEASE_TTL - timedelta(minutes=1)
    for job in (stale, old_but_leased):
        job.created_at = long_ago
    catalog.session.commit()
    _lease(catalog, old_but_leased.id, "MCK00001", ttl=timedelta(minutes=15))
    library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)

    report = recover_after_restart(catalog, library)

    assert report.stale_pending_job_ids == [stale.id]
    for job_id in (stale.id, fresh.id):
        refreshed = catalog.get_job(job_id)
        assert refreshed is not None
        assert refreshed.state == "pending"
