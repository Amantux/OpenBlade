from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'aml-access.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture
def admin_session(client: TestClient) -> dict[str, str | None]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    return {"sessionID": response.cookies.get("sessionID")}


def test_access_endpoints_require_admin_auth(client: TestClient) -> None:
    assert client.get("/aml/access/devices").status_code == 401


def test_license_routes_seed_demo_licenses(client: TestClient, admin_session: dict[str, str | None]) -> None:
    response = client.get("/aml/access/licenses", cookies=admin_session)
    assert response.status_code == 200
    serials = {item["serialNumber"] for item in response.json()["license"]}
    assert {"LIC-BASE-001", "LIC-PART-001"}.issubset(serials)


def test_group_host_and_device_lifecycle(client: TestClient, admin_session: dict[str, str | None]) -> None:
    devices_response = client.get("/aml/access/devices", cookies=admin_session)
    assert devices_response.status_code == 200
    devices = devices_response.json()["device"]
    if not devices:
        pytest.skip("No AML devices available in simulator inventory")
    serial_number = devices[0]["serialNumber"]

    create_group = client.post("/aml/access/group/fabric-a", cookies=admin_session)
    assert create_group.status_code == 201
    assert create_group.json()["accessGroup"]["name"] == "fabric-a"

    create_host = client.post(
        "/aml/access/hosts",
        json={"WWPN": "10:00:00:00:00:00:00:01", "alias": "host-a"},
        cookies=admin_session,
    )
    assert create_host.status_code == 201
    assert create_host.json()["host"]["WWPN"] == "10:00:00:00:00:00:00:01"

    add_device = client.post(
        "/aml/access/group/fabric-a/device",
        json={"serialNumber": serial_number},
        cookies=admin_session,
    )
    assert add_device.status_code == 200

    add_host = client.post(
        "/aml/access/group/fabric-a/hosts",
        json={"WWPN": "10:00:00:00:00:00:00:01"},
        cookies=admin_session,
    )
    assert add_host.status_code == 200

    group_response = client.get("/aml/access/group/fabric-a", cookies=admin_session)
    assert group_response.status_code == 200
    group = group_response.json()["accessGroup"]
    assert serial_number in group["devices"]
    assert "10:00:00:00:00:00:00:01" in group["hosts"]

    host_response = client.get("/aml/access/host/10:00:00:00:00:00:00:01", cookies=admin_session)
    assert host_response.status_code == 200
    assert "fabric-a" in host_response.json()["host"]["groups"]
