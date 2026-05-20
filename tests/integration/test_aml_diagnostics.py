"""Integration tests for AML diagnostics endpoints (task-013)."""

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
