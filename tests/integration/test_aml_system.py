from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'sys-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


@pytest.fixture()
def service_client(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "service", "password": "service123"})
    assert resp.status_code == 200
    return client


@pytest.mark.parametrize(
    "path",
    [
        "/aml/system/info",
        "/aml/system/time",
        "/aml/system/snmp",
        "/aml/network/interfaces",
        "/aml/system/backup",
    ],
)
def test_system_endpoints_require_auth(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/aml/system/time", {"utc": "2024-01-15T06:00:00Z"}),
        ("put", "/aml/system/security", {"loginBanner": "restricted"}),
        ("put", "/aml/system/ha", {"enabled": True}),
    ],
)
def test_system_mutations_require_admin(service_client: TestClient, method: str, path: str, payload: dict[str, object]) -> None:
    resp = getattr(service_client, method)(path, json=payload)
    assert resp.status_code == 403


def test_system_info_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/info")
    assert resp.status_code == 200
    assert "systemDetail" in resp.json()


def test_system_time_rejects_invalid_timestamp(authed: TestClient) -> None:
    resp = authed.get("/aml/system/time")
    assert resp.status_code == 200

    invalid = authed.put("/aml/system/time", json={"utc": "not-a-timestamp"})
    assert invalid.status_code == 422

    follow_up = authed.get("/aml/system/time")
    assert follow_up.status_code == 200


def test_audit_can_be_cleared_without_reinsertion(authed: TestClient) -> None:
    resp = authed.get("/aml/system/audit")
    assert resp.status_code == 200

    cleared = authed.delete("/aml/system/audit")
    assert cleared.status_code == 200

    after = authed.get("/aml/system/audit")
    assert after.status_code == 200
    assert after.json()["auditList"]["audit"] == []


def test_certificates_delete_then_get_still_works(authed: TestClient) -> None:
    resp = authed.get("/aml/system/certificates")
    assert resp.status_code == 200
    certs = resp.json()["certList"]["cert"]

    if certs:
        deleted = authed.delete(f"/aml/system/certificate/{certs[0]['name']}")
        assert deleted.status_code == 200

    follow_up = authed.get("/aml/system/certificates")
    assert follow_up.status_code == 200
    assert isinstance(follow_up.json()["certList"]["cert"], list)


def test_preferences_unknown_field_returns_422(authed: TestClient) -> None:
    resp = authed.get("/aml/system/preferences")
    assert resp.status_code == 200

    invalid = authed.put("/aml/system/preferences", json={"unknownField": True})
    assert invalid.status_code == 422


def test_snmp_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/snmp")
    assert resp.status_code == 200
    assert "snmpConfig" in resp.json()


def test_network_interfaces_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/network/interfaces")
    assert resp.status_code == 200
    assert "interfaceList" in resp.json()


def test_backup_status_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/backup")
    assert resp.status_code == 200
    assert "backupStatus" in resp.json()


def test_reset_clears_system_config(tmp_path: Path) -> None:
    from openblade.api.aml_state import get_aml_system_config, set_aml_system_config
    from openblade.bootstrap import create_context, reset_context
    from openblade.config import OpenBladeConfig

    ctx = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'r.db'}"))
    reset_context(ctx)
    cfg = get_aml_system_config()
    cfg["hostname"] = "mutated-host"
    set_aml_system_config(cfg)
    reset_context(ctx)
    assert get_aml_system_config()["hostname"] == "openblade-1", "reset_context must clear system config"
