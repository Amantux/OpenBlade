"""Integration tests for AML diagnostics endpoints (task-013)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'diag-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


# ---------------------------------------------------------------------------
# Drive cleaning reports
# ---------------------------------------------------------------------------

def test_drive_cleaning_report_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/drives/reports/cleaning")
    assert resp.status_code == 401


def test_drive_cleaning_report_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drives/reports/cleaning")
    assert resp.status_code == 200


def test_drive_cleaning_email_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/drives/reports/cleaning/email", json={"email": {"recipient": "a@b.com"}})
    assert resp.status_code == 401


def test_drive_cleaning_email_returns_200(authed: TestClient) -> None:
    resp = authed.post(
        "/aml/drives/reports/cleaning/email",
        json={"recipients": ["admin@example.com"]},
    )
    assert resp.status_code == 200
    assert resp.json().get("code") == 0


# ---------------------------------------------------------------------------
# Drive clean tasks
# ---------------------------------------------------------------------------

def test_list_clean_tasks_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/drive/DRV-001/operations/clean")
    assert resp.status_code == 401


def test_list_clean_tasks_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/clean")
    assert resp.status_code == 200


def test_list_clean_tasks_404_on_missing_drive(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/MISSING-DRIVE/operations/clean")
    assert resp.status_code == 404


def test_start_clean_task_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/drive/DRV-001/operations/clean", json={})
    assert resp.status_code == 401


def test_start_clean_task_returns_200(authed: TestClient) -> None:
    resp = authed.post(
        "/aml/drive/DRV-001/operations/clean",
        json={"cleanDriveTask": {"serialNumber": "DRV-001", "coordinate": "slot-1"}},
    )
    assert resp.status_code in {200, 422}


def test_get_clean_task_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/clean/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Drive load/unload tasks
# ---------------------------------------------------------------------------

def test_list_load_tasks_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/load")
    assert resp.status_code == 200


def test_get_load_task_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/load/nonexistent")
    assert resp.status_code == 404


def test_list_unload_tasks_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/unload")
    assert resp.status_code == 200


def test_get_unload_task_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/operations/unload/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Physical library elements
# ---------------------------------------------------------------------------

def test_physical_elements_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/physicalLibrary/elements")
    assert resp.status_code == 401


def test_physical_elements_returns_list(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/elements")
    assert resp.status_code == 200


def test_physical_element_address_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/elements/INVALID-ADDR")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Robotics — route ordering regression
# ---------------------------------------------------------------------------

def test_robotics_list_returns_200(authed: TestClient) -> None:
    """Static /physicalLibrary/robotics must not be swallowed by /{id}."""
    resp = authed.get("/aml/physicalLibrary/robotics")
    assert resp.status_code == 200


def test_robotics_detail_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/robotics/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Towers & magazines
# ---------------------------------------------------------------------------

def test_towers_list_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/towers")
    assert resp.status_code == 200


def test_tower_detail_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/towers/nonexistent")
    assert resp.status_code == 404


def test_magazines_list_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/magazines")
    assert resp.status_code == 200


def test_magazine_detail_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/physicalLibrary/magazines/nonexistent")
    assert resp.status_code == 404


def test_physical_elements_use_bay_coordinates_without_duplicates(authed: TestClient) -> None:
    response = authed.get("/aml/physicalLibrary/elements")
    assert response.status_code == 200

    body = response.json()
    elements = body["elementList"]["element"]
    slot_coordinates = [str(item["coordinate"]) for item in elements if item.get("type") == "slot"]
    assert len(slot_coordinates) == 50
    assert len(slot_coordinates) == len(set(slot_coordinates))
    assert sum(1 for coordinate in slot_coordinates if coordinate.startswith("1,1,")) == 25
    assert sum(1 for coordinate in slot_coordinates if coordinate.startswith("1,2,")) == 25


def test_magazine_slots_expose_library_coordinates(authed: TestClient) -> None:
    response = authed.get("/aml/magazine/MAG-1/slots")
    assert response.status_code == 200

    slots = response.json()["slotList"]["slot"]
    assert len(slots) == 25
    first = slots[0]
    assert first["address"] == "MAG-1,1"
    assert first["libraryCoordinate"] == "1,1,1"
    assert re.fullmatch(r"[A-Z0-9]{8}", str(first.get("barcode", "") or "EMPTY000")) is not None


def test_magazine_slots_match_media_coordinates(authed: TestClient) -> None:
    media_response = authed.get("/aml/media")
    assert media_response.status_code == 200
    media_items = media_response.json()["mediaList"]["media"]
    media_by_coordinate = {
        str(item["slotAddress"]): str(item["barcode"])
        for item in media_items
        if item.get("slotAddress")
    }

    for magazine_id in ("MAG-1", "MAG-2"):
        slots_response = authed.get(f"/aml/magazine/{magazine_id}/slots")
        assert slots_response.status_code == 200
        slots = slots_response.json()["slotList"]["slot"]

        occupied = 0
        for slot in slots:
            coordinate = str(slot["libraryCoordinate"])
            expected_barcode = media_by_coordinate.get(coordinate)
            assert slot.get("barcode") == expected_barcode
            assert slot.get("state") == ("occupied" if expected_barcode else "empty")
            if expected_barcode:
                occupied += 1

        magazine_response = authed.get(f"/aml/magazine/{magazine_id}")
        assert magazine_response.status_code == 200
        assert int(magazine_response.json()["magazine"]["occupiedSlots"]) == occupied


def test_cleaning_media_covers_all_drive_generations(authed: TestClient) -> None:
    drives_response = authed.get("/aml/drives")
    assert drives_response.status_code == 200
    drives = drives_response.json()["driveList"]["drive"]

    media_response = authed.get("/aml/media")
    assert media_response.status_code == 200
    media_items = media_response.json()["mediaList"]["media"]

    drive_counts: dict[str, int] = {}
    for drive in drives:
        drive_type = str(drive["type"])
        drive_counts[drive_type] = drive_counts.get(drive_type, 0) + 1

    cleaning_counts: dict[str, int] = {}
    for media in media_items:
        media_type = str(media.get("type", ""))
        if not media_type.endswith("-CLN"):
            continue
        cleaning_counts[media_type] = cleaning_counts.get(media_type, 0) + 1

    for drive_type, count in drive_counts.items():
        assert cleaning_counts.get(f"{drive_type}-CLN", 0) >= count

    reports_response = authed.get("/aml/drives/reports/cleaning")
    assert reports_response.status_code == 200
    reports = reports_response.json()["driveCleaningList"]["driveCleaning"]
    report_serials = [str(item["serialNumber"]) for item in reports]
    assert len(report_serials) == len(drives)
    assert len(set(report_serials)) == len(drives)
    for serial in report_serials:
        assert serial.startswith("DRV-")


# ---------------------------------------------------------------------------
# Diagnostics tests — route ordering regression
# ---------------------------------------------------------------------------

def test_diagnostics_tests_list_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/diagnostics/tests")
    assert resp.status_code == 401


def test_diagnostics_tests_list_returns_200(authed: TestClient) -> None:
    """Static /diagnostics/tests must be reachable (ordering regression check)."""
    resp = authed.get("/aml/diagnostics/tests")
    assert resp.status_code == 200


def test_diagnostics_run_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/diagnostics/tests/run", json={})
    assert resp.status_code == 401


def test_diagnostics_run_returns_200(authed: TestClient) -> None:
    resp = authed.post("/aml/diagnostics/tests/run", json={})
    assert resp.status_code in {200, 422}


def test_diagnostics_results_returns_200(authed: TestClient) -> None:
    """Static /diagnostics/tests/results must not be shadowed by /{id}."""
    resp = authed.get("/aml/diagnostics/tests/results")
    assert resp.status_code == 200


def test_diagnostics_result_by_id_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/diagnostics/tests/results/nonexistent")
    assert resp.status_code == 404
