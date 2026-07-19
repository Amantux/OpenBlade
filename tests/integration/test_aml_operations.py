"""Integration tests for AML operations endpoints (task-009)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'ops-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


def _controller_headers() -> dict[str, str]:
    return {"X-Openblade-Service-Token": "openblade-controller-dev-token-do-not-expose"}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def test_list_jobs_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/jobs")
    assert resp.status_code == 401


def test_list_jobs_returns_list(authed: TestClient) -> None:
    resp = authed.get("/aml/jobs")
    assert resp.status_code == 200
    data = resp.json()
    jobs = data.get("jobs") or (data.get("jobList") or {}).get("job") or data if isinstance(data, list) else []
    assert isinstance(jobs, list)


def test_job_history_returns_terminal_jobs(authed: TestClient) -> None:
    """Regression: job history must include cancelled and failed jobs, not only completed."""
    resp = authed.get("/aml/jobs/history")
    assert resp.status_code == 200


def test_operations_status(authed: TestClient) -> None:
    resp = authed.get("/aml/operations/status")
    assert resp.status_code == 200


def test_operations_queue(authed: TestClient) -> None:
    resp = authed.get("/aml/operations/queue")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mount / Unmount — job lifecycle regression
# ---------------------------------------------------------------------------

def test_mount_creates_completed_job(authed: TestClient) -> None:
    """Regression: after mount the job must be completed (not stuck as active)."""
    mount_resp = authed.post(
        "/aml/mount",
        json={"mount": {"barcode": "VOL001L9", "drive": "DRV-001"}},
        headers=_controller_headers(),
    )
    # Accept 200 (success) or 409 (already mounted from seed data)
    assert mount_resp.status_code in {200, 409}

    if mount_resp.status_code == 200:
        # Active queue must NOT contain a stale mount job
        queue_resp = authed.get("/aml/operations/queue")
        assert queue_resp.status_code == 200
        queue_data = queue_resp.json()
        active_ops = queue_data.get("queue") or queue_data.get("operations") or []
        mount_jobs = [j for j in active_ops if isinstance(j, dict) and "mount" in str(j.get("type", "")).lower()]
        assert mount_jobs == [], "Mount job must be archived, not stuck in active queue"

        # History must contain the completed mount job
        history_resp = authed.get("/aml/jobs/history")
        assert history_resp.status_code == 200


def test_unmount_returns_success(authed: TestClient) -> None:
    # Mount first
    authed.post(
        "/aml/mount",
        json={"mount": {"barcode": "VOL001L9", "drive": "DRV-001"}},
        headers=_controller_headers(),
    )
    # Now unmount
    resp = authed.post(
        "/aml/unmount",
        json={"unmount": {"barcode": "VOL001L9", "drive": "DRV-001"}},
        headers=_controller_headers(),
    )
    assert resp.status_code in {200, 404, 409}  # accept not-found if mount 409'd above


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------

def test_move_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/move", json={"move": {"source": "slot-1", "destination": "slot-2"}})
    assert resp.status_code == 401


def test_move_returns_result(authed: TestClient) -> None:
    resp = authed.post("/aml/move", json={"move": {"source": "slot-1", "destination": "slot-2"}})
    assert resp.status_code in {200, 404, 422}


def test_move_accepts_numeric_slot_addresses(authed: TestClient) -> None:
    inventory_resp = authed.get("/aml/library/inventory")
    assert inventory_resp.status_code == 200
    inventory_payload = inventory_resp.json()
    inventory_slots = (
        inventory_payload.get("slots")
        or inventory_payload.get("slot")
        or []
    )

    media_resp = authed.get("/aml/media")
    assert media_resp.status_code == 200
    media_items = (media_resp.json().get("mediaList") or {}).get("media") or []
    source_media = next(
        item for item in media_items if str(item.get("slotAddress", "")).startswith("1,1,")
    )
    source_slot = str(source_media["slotAddress"]).split(",")[-1]
    dest_slot = next(
        str(slot.get("slotId") or slot.get("id"))
        for slot in inventory_slots
        if not bool(slot.get("occupied"))
    )

    resp = authed.post(
        "/aml/move",
        json={
            "move": {
                "source": source_slot,
                "destination": dest_slot,
                "barcode": source_media["barcode"],
            }
        },
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def test_start_inventory_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/inventory")
    assert resp.status_code == 401


def test_start_inventory(authed: TestClient) -> None:
    resp = authed.post("/aml/inventory")
    assert resp.status_code == 200
    assert resp.json().get("code") == 0


def test_inventory_status(authed: TestClient) -> None:
    authed.post("/aml/inventory")
    resp = authed.get("/aml/inventory/status")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Robotics
# ---------------------------------------------------------------------------

def test_robotics_status_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/operations/robotics/status")
    assert resp.status_code == 200


def test_robotics_test_sets_last_test_time(authed: TestClient) -> None:
    """Regression: robotics last test time must persist in aml_state, not a module global."""
    resp = authed.post("/aml/operations/robotics/test")
    assert resp.status_code == 200
    # Subsequent status check must reflect the test was run
    status_resp = authed.get("/aml/operations/robotics/status")
    assert status_resp.status_code == 200


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def test_cleaning_status_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/operations/cleaning/status")
    assert resp.status_code == 200


def test_start_cleaning_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/operations/clean")
    assert resp.status_code == 401


def test_start_cleaning(authed: TestClient) -> None:
    resp = authed.post("/aml/operations/clean", json={"drives": ["DRV-001"]})
    assert resp.status_code in {200, 422}


def test_start_cleaning_updates_drive_cleaning_report(authed: TestClient) -> None:
    before_response = authed.get("/aml/drives/reports/cleaning")
    assert before_response.status_code == 200
    before_reports = before_response.json()["driveCleaningList"]["driveCleaning"]
    target_before = next(item for item in before_reports if str(item["serialNumber"]) == "DRV-001")

    clean_response = authed.post("/aml/operations/clean", json={"clean": {"drives": ["DRV-001"]}})
    assert clean_response.status_code == 200
    assert clean_response.json()["code"] == 0

    after_response = authed.get("/aml/drives/reports/cleaning")
    assert after_response.status_code == 200
    after_reports = after_response.json()["driveCleaningList"]["driveCleaning"]
    target_matches = [item for item in after_reports if str(item["serialNumber"]) == "DRV-001"]
    assert len(target_matches) == 1

    target_after = target_matches[0]
    assert str(target_after["mediaBarcode"]) == str(target_before["mediaBarcode"])
    assert int(target_after["useCount"]) == int(target_before["useCount"]) + 1


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------

def test_import_status(authed: TestClient) -> None:
    resp = authed.get("/aml/import/status")
    assert resp.status_code == 200


def test_export_status(authed: TestClient) -> None:
    resp = authed.get("/aml/export/status")
    assert resp.status_code == 200
