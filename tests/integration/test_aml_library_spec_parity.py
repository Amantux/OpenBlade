"""Integration parity coverage for documented library/physicalLibrary AML endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'library-parity.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


def _library_matrix_endpoints() -> set[tuple[str, str]]:
    matrix_path = Path(__file__).resolve().parents[2] / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
    matrix = json.loads(matrix_path.read_text())
    return {
        (str(item["method"]).upper(), str(item["path"]))
        for item in matrix["endpoints"]
        if "/library" in str(item["path"]).lower() or "/physicallibrary" in str(item["path"]).lower()
    }


def _app_endpoints() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in {"GET", "POST", "PUT", "DELETE"}:
                routes.add((method, route.path))
    return routes


def test_library_matrix_endpoints_are_implemented() -> None:
    missing = sorted(_library_matrix_endpoints() - _app_endpoints())
    assert missing == []


def test_library_operation_task_lifecycle(authed: TestClient) -> None:
    create = authed.post("/aml/physicalLibrary/operations/reboot")
    assert create.status_code == 200
    assert create.json()["code"] == 0

    listed = authed.get("/aml/physicalLibrary/operations/reboot")
    assert listed.status_code == 200
    reboot_tasks = listed.json()["taskList"]["task"]
    assert reboot_tasks
    task_id = reboot_tasks[0]["id"]

    fetched = authed.get(f"/aml/physicalLibrary/operations/reboot/{task_id}")
    assert fetched.status_code == 200
    assert fetched.json()["task"]["type"] == "reboot"

    deleted = authed.delete(f"/aml/physicalLibrary/operations/reboot/{task_id}")
    assert deleted.status_code == 200
    assert deleted.json()["code"] == 0

    missing = authed.get(f"/aml/physicalLibrary/operations/reboot/{task_id}")
    assert missing.status_code == 404


def test_inventory_operation_updates_inventory_status(authed: TestClient) -> None:
    started = authed.post("/aml/physicalLibrary/operations/inventory")
    assert started.status_code == 200
    assert started.json()["code"] == 0

    inventory_status = authed.get("/aml/inventory/status")
    assert inventory_status.status_code == 200
    payload = inventory_status.json()["inventoryStatus"]
    assert payload["state"] == "completed"
    assert int(payload["progress"]) == 100


def test_segments_cleaning_and_amp_round_trip(authed: TestClient) -> None:
    segments_resp = authed.get("/aml/physicalLibrary/segments")
    assert segments_resp.status_code == 200
    segments = segments_resp.json()["segmentList"]["segment"]
    assert segments
    target_id = str(segments[0]["id"])

    amp_set = authed.put("/aml/physicalLibrary/segments/amp", json={"segmentList": {"segment": [{"id": target_id}]}})
    assert amp_set.status_code == 200
    assert amp_set.json()["code"] == 0

    amp_segments = authed.get("/aml/physicalLibrary/segments/amp")
    assert amp_segments.status_code == 200
    assert any(str(item["id"]) == target_id for item in amp_segments.json()["segmentList"]["segment"])

    cleaning_set = authed.post("/aml/physicalLibrary/segments/cleaning", json={"segment": {"id": target_id}})
    assert cleaning_set.status_code == 200
    assert cleaning_set.json()["code"] == 0

    cleaning_segments = authed.get("/aml/physicalLibrary/segments/cleaning")
    assert cleaning_segments.status_code == 200
    assert any(str(item["id"]) == target_id for item in cleaning_segments.json()["segmentList"]["segment"])

    cleaning_delete = authed.request("DELETE", "/aml/physicalLibrary/segments/cleaning", json={"segment": {"id": target_id}})
    assert cleaning_delete.status_code == 200
    assert cleaning_delete.json()["code"] == 0


def test_subset_and_environment_endpoints(authed: TestClient) -> None:
    configuration = authed.get("/aml/physicalLibrary/subset/configuration")
    assert configuration.status_code == 200
    assert "physicalLibraryConfiguration" in configuration.json()

    remote_access = authed.get("/aml/physicalLibrary/subset/remoteAccess")
    assert remote_access.status_code == 200
    assert "physicalLibraryRemoteAccess" in remote_access.json()

    resources = authed.get("/aml/physicalLibrary/subset/resources")
    assert resources.status_code == 200
    assert "physicalLibraryResources" in resources.json()

    settings = authed.get("/aml/physicalLibrary/subset/settings")
    assert settings.status_code == 200
    assert "physicalLibrarySettings" in settings.json()

    email = authed.post(
        "/aml/physicalLibrary/environment/email",
        json={"recipients": ["ops@example.com"], "subject": "daily env report"},
    )
    assert email.status_code == 200
    assert email.json()["code"] == 0


def test_library_blade_serial_endpoint(authed: TestClient) -> None:
    ok = authed.get("/aml/devices/blade/library/MGMT0001")
    assert ok.status_code == 200
    assert ok.json()["mgmtBlade"]["serialNumber"] == "MGMT0001"

    missing = authed.get("/aml/devices/blade/library/MISSING")
    assert missing.status_code == 404


def test_new_library_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/aml/physicalLibrary/operations").status_code == 401
    assert client.post("/aml/physicalLibrary/operations/inventory").status_code == 401
    assert client.put("/aml/physicalLibrary/segments/amp", json={"segmentList": {"segment": [{"id": "SEG-ST-001"}]}}).status_code == 401
    assert client.request(
        "DELETE",
        "/aml/physicalLibrary/segments/cleaning",
        json={"segment": {"id": "SEG-CLN-001"}},
    ).status_code == 401
