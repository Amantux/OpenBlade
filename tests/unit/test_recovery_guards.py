"""Pin recovery guards that mutation testing showed were unasserted.

Each test fails when the corresponding guard in openblade/jobs/recovery.py is
mutated (and->or on the owner check, the once-per-job dedupe, the journal
payload, the stale-age boundary and its timezone normalisation).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from openblade.bootstrap import create_context
from openblade.catalog.repository import CatalogRepository
from openblade.config import OpenBladeConfig
from openblade.jobs import recovery
from openblade.jobs.recovery import INTERRUPTED_ERROR, recover_after_restart
from openblade.simulator.library import MockLibraryBackend

EXPIRED = timedelta(seconds=-1)


def _catalog(tmp_path: Path) -> CatalogRepository:
    return create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}")).catalog


def _library() -> MockLibraryBackend:
    return MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)


def test_recovery_expired_lease_of_pending_job_leaves_job_untouched(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    job = catalog.create_job("archive", {})
    leases = catalog.acquire_drive_leases(
        job_id=job.id, barcodes=["MCK00001"], num_drives=2, ttl=EXPIRED
    )
    assert leases is not None

    report = recover_after_restart(catalog, _library())

    assert report.interrupted_job_ids == []
    refreshed = catalog.get_job(job.id)
    assert refreshed is not None
    assert refreshed.state == "pending"
    assert [e.event for e in catalog.job_journal(job.id)].count("recovered") == 0


def test_recovery_job_with_two_expired_leases_is_failed_once(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    job = catalog.create_job("archive", {})
    catalog.update_job_state(job.id, "running")
    leases = catalog.acquire_drive_leases(
        job_id=job.id, barcodes=["MCK00001", "MCK00002"], num_drives=2, ttl=EXPIRED
    )
    assert leases is not None
    assert len(leases) == 2

    report = recover_after_restart(catalog, _library())

    assert report.interrupted_job_ids == [job.id]
    recovered = [e for e in catalog.job_journal(job.id) if e.event == "recovered"]
    assert len(recovered) == 1
    assert json.loads(recovered[0].detail_json) == {
        "lease_id": leases[0].id,
        "error": INTERRUPTED_ERROR,
    }


def test_is_older_than_exact_ttl_boundary_is_not_stale() -> None:
    now = datetime(2026, 10, 10, 12, 0, 0)
    ttl = timedelta(minutes=15)

    assert recovery._is_older_than(now - ttl, ttl, now) is False
    assert recovery._is_older_than(now - ttl - timedelta(microseconds=1), ttl, now) is True


def test_is_older_than_normalises_aware_timestamp_to_utc() -> None:
    now = datetime(2026, 10, 10, 12, 0, 0)
    ttl = timedelta(minutes=15)
    plus_five = timezone(timedelta(hours=5))
    # 16:50+05:00 == 11:50 UTC: 10 min old -> fresh. Read naively it is in the future.
    fresh = datetime(2026, 10, 10, 16, 50, tzinfo=plus_five)
    # 16:40+05:00 == 11:40 UTC: 20 min old -> stale. Read naively (16:40) it is fresh.
    stale = datetime(2026, 10, 10, 16, 40, tzinfo=plus_five)

    assert recovery._is_older_than(fresh, ttl, now) is False
    assert recovery._is_older_than(stale, ttl, now) is True
    assert recovery._is_older_than(datetime(2026, 10, 10, 11, 50, tzinfo=UTC), ttl, now) is False
