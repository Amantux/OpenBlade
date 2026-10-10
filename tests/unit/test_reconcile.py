"""Drives with unknown physical state (failed unmount/unload) must be reconciled before reuse."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openblade.api import routes_jobs
from openblade.bootstrap import AppContext, create_context, get_context
from openblade.config import OpenBladeConfig
from openblade.domain.errors import DriveUnreconciledError
from openblade.domain.models import MountState
from openblade.jobs.reconcile import (
    DRIVE_RECONCILED,
    PHYSICAL_STATE_UNKNOWN,
    drives_pending_reconciliation,
    reconcile_drive,
)
from openblade.jobs.restore import _load_if_needed
from openblade.jobs.scheduler import DriveHandle, DriveScheduler, InMemoryLeaseStore
from openblade.simulator.library import MockLibraryBackend


def _context(tmp_path: Path) -> AppContext:
    return create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))


def _mark_unknown(context: AppContext, drive: int | None, barcode: str = "MCK00001") -> None:
    context.catalog.journal(
        "job-1", PHYSICAL_STATE_UNKNOWN, {"op": "unload", "barcode": barcode, "drive": drive}
    )


def test_pending_set_tracks_unknown_and_reconciled(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _mark_unknown(context, 1)
    _mark_unknown(context, None)  # no drive named: ignored
    pending = drives_pending_reconciliation(context.catalog)
    assert set(pending) == {1}
    assert pending[1].barcode == "MCK00001"
    assert pending[1].op == "unload"
    assert pending[1].job_id == "job-1"
    context.catalog.journal("recovery", DRIVE_RECONCILED, {"drive": 1})
    assert drives_pending_reconciliation(context.catalog) == {}
    _mark_unknown(context, 1)  # a later failure makes it pending again
    assert set(drives_pending_reconciliation(context.catalog)) == {1}


def test_scheduler_skips_unreconciled_drive_and_refuses_when_none_left() -> None:
    store = InMemoryLeaseStore()
    store.unreconciled = {0: "MCK00001"}
    scheduler = DriveScheduler(2, store=store)
    [handle] = scheduler.acquire_drives(["MCK00002"], timeout=0.1)
    assert handle.drive_id == 1
    scheduler.release_drives([handle])

    store.unreconciled = {0: "MCK00001", 1: None}
    with pytest.raises(DriveUnreconciledError, match="drive 0 .*MCK00001"):
        scheduler.acquire_drives(["MCK00002"], timeout=0.1)


def test_load_if_needed_refuses_unreconciled_drive(tmp_path: Path) -> None:
    context = _context(tmp_path)
    barcode = next(str(slot.barcode) for slot in context.library.inventory().slots if slot.barcode)
    _mark_unknown(context, 0, barcode="MCK99999")
    handle = DriveHandle(drive_id=0, barcode=barcode)
    with pytest.raises(DriveUnreconciledError, match="Drive 0"):
        _load_if_needed(context.catalog, context.library, context.ltfs, handle, "job-2")
    assert context.library.find_drive_by_barcode(barcode) is None  # nothing was loaded


def test_reconcile_drive_refuses_while_mounted(tmp_path: Path) -> None:
    context = _context(tmp_path)
    library = MockLibraryBackend(num_slots=4, num_drives=2, num_import_export_slots=1)
    _mark_unknown(context, 1)
    library._drives[1].mount_state = MountState.MOUNTED_RW
    with pytest.raises(DriveUnreconciledError, match="still mounted_rw"):
        reconcile_drive(context.catalog, library, 1)
    assert set(drives_pending_reconciliation(context.catalog)) == {1}

    library._drives[1].mount_state = MountState.UNMOUNTED
    done = reconcile_drive(context.catalog, library, 1)
    assert done.drive_id == 1
    assert done.op == DRIVE_RECONCILED
    assert drives_pending_reconciliation(context.catalog) == {}


def test_reconcile_api_round_trip(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _mark_unknown(context, 1)
    app = FastAPI()
    app.include_router(routes_jobs.router, prefix="/jobs")
    app.dependency_overrides[get_context] = lambda: context
    client = TestClient(app)

    context.library._drives[1].mount_state = MountState.MOUNTED_RO  # type: ignore[attr-defined]
    conflict = client.post("/jobs/recovery/reconcile/1")
    assert conflict.status_code == 409
    assert "Drive 1" in conflict.json()["detail"]

    context.library._drives[1].mount_state = MountState.UNMOUNTED  # type: ignore[attr-defined]
    ok = client.post("/jobs/recovery/reconcile/1")
    assert ok.status_code == 200
    assert ok.json()["drive"] == 1
    assert drives_pending_reconciliation(context.catalog) == {}

    assert client.post("/jobs/recovery/reconcile/99").status_code == 404
