"""Protocol gateway isolation and auth tests."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas import protocol_gateway as protocol_gateway_module
from openblade.nas.protocol_gateway import GatewayStatus, ProtocolGateway


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'protocol-gateway.db'}"))
    reset_context(context)
    monkeypatch.setattr(protocol_gateway_module, "_gateway", ProtocolGateway())


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


@pytest.fixture
def gw() -> ProtocolGateway:
    return ProtocolGateway()


def test_gateway_starts_stopped(gw: ProtocolGateway) -> None:
    assert gw.status == GatewayStatus.STOPPED


def test_start_stop(gw: ProtocolGateway) -> None:
    gw.start()
    assert gw.status == GatewayStatus.RUNNING
    gw.stop()
    assert gw.status == GatewayStatus.STOPPED


def test_add_credential(gw: ProtocolGateway) -> None:
    cred = gw.add_credential("sftp_user", "secret123")
    assert cred.username == "sftp_user"
    assert cred.enabled is True


def test_duplicate_credential_rejected(gw: ProtocolGateway) -> None:
    gw.add_credential("dup_user", "pass1")
    with pytest.raises(ValueError, match="already exists"):
        gw.add_credential("dup_user", "pass2")


def test_authenticate_valid(gw: ProtocolGateway) -> None:
    gw.add_credential("alice", "correctpass")
    result = gw.authenticate("alice", "correctpass")
    assert result is not None
    assert result.username == "alice"


def test_authenticate_wrong_password(gw: ProtocolGateway) -> None:
    gw.add_credential("bob", "rightpass")
    result = gw.authenticate("bob", "wrongpass")
    assert result is None


def test_authenticate_unknown_user(gw: ProtocolGateway) -> None:
    result = gw.authenticate("ghost", "anypass")
    assert result is None


def test_authenticate_disabled_credential(gw: ProtocolGateway) -> None:
    gw.add_credential("disabled_user", "pass")
    gw.update_credential("disabled_user", enabled=False)
    result = gw.authenticate("disabled_user", "pass")
    assert result is None


def test_timing_safe_comparison(gw: ProtocolGateway) -> None:
    del gw
    src = inspect.getsource(protocol_gateway_module.GatewayCredential.verify_password)
    assert "compare_digest" in src


def test_path_isolation(gw: ProtocolGateway) -> None:
    gw.add_credential("restricted", "pass", allowed_paths=["/openblade/inbox"])
    assert gw.check_path_allowed("restricted", "/openblade/inbox") is True
    assert gw.check_path_allowed("restricted", "/openblade/inbox-critical") is False
    assert gw.check_path_allowed("restricted", "/openblade/restore") is False


def test_path_isolation_critical(gw: ProtocolGateway) -> None:
    gw.add_credential("critical_user", "pass", allowed_paths=["/openblade/inbox-critical"])
    assert gw.check_path_allowed("critical_user", "/openblade/inbox-critical") is True
    assert gw.check_path_allowed("critical_user", "/openblade/inbox") is False


def test_route_upload_path_uses_inbox_root(gw: ProtocolGateway) -> None:
    gw.add_credential("router", "pass", allowed_paths=["/openblade/inbox-critical"])
    routed = gw.route_upload_path("router", "/openblade/inbox-critical/folder/file.bin")
    assert routed.endswith("/inbox-critical/folder/file.bin")


def test_session_upload_audit(gw: ProtocolGateway) -> None:
    gw.add_credential("session_user", "pass")
    session = gw.open_session("session_user", "192.168.1.100")
    upload = gw.record_upload(session.session_id, "/openblade/inbox/archive.tar", 1024)
    assert upload.requested_path == "/openblade/inbox/archive.tar"
    assert session.files_uploaded == 1
    assert session.bytes_uploaded == 1024
    assert session.uploads[0].routed_path.endswith("/inbox/archive.tar")


def test_session_lifecycle(gw: ProtocolGateway) -> None:
    gw.add_credential("session_user", "pass")
    session = gw.open_session("session_user", "192.168.1.100")
    assert session.session_id is not None
    assert session.disconnected_at is None

    active = gw.list_sessions(active_only=True)
    assert any(s.session_id == session.session_id for s in active)

    gw.close_session(session.session_id, bytes_uploaded=1024, files_uploaded=3)
    active = gw.list_sessions(active_only=True)
    assert not any(s.session_id == session.session_id for s in active)


def test_remove_credential(gw: ProtocolGateway) -> None:
    gw.add_credential("to_remove", "pass")
    assert gw.remove_credential("to_remove") is True
    assert gw.authenticate("to_remove", "pass") is None


def test_remove_nonexistent(gw: ProtocolGateway) -> None:
    assert gw.remove_credential("nobody") is False


def test_stats(gw: ProtocolGateway) -> None:
    stats = gw.get_stats()
    assert "status" in stats
    assert "total_sessions" in stats
    assert "active_sessions" in stats


def test_gateway_config_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/gateway/config", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "bind_port" in data
    assert "status" in data


def test_gateway_status_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/gateway/status", headers=admin_auth_headers)
    assert resp.status_code == 200


def test_gateway_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/gateway/config")
    assert resp.status_code in (401, 403)


def test_credential_lifecycle_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    resp = client.post(
        "/api/gateway/credentials",
        json={
            "username": "sftp_api_user",
            "password": "testpass123",
            "allowed_paths": ["/openblade/inbox"],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200

    resp = client.get("/api/gateway/credentials", headers=admin_auth_headers)
    assert resp.status_code == 200
    users = [cred["username"] for cred in resp.json()]
    assert "sftp_api_user" in users

    resp = client.delete("/api/gateway/credentials/sftp_api_user", headers=admin_auth_headers)
    assert resp.status_code == 200


def test_duplicate_credential_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    client.post(
        "/api/gateway/credentials",
        json={"username": "dup_api_user", "password": "pass"},
        headers=admin_auth_headers,
    )
    resp = client.post(
        "/api/gateway/credentials",
        json={"username": "dup_api_user", "password": "pass2"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409


def test_start_stop_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    resp = client.post("/api/gateway/start", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    resp = client.post("/api/gateway/stop", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_inbox_paths_api(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    resp = client.get("/api/gateway/inbox-paths", headers=admin_auth_headers)
    assert resp.status_code == 200
    paths = [path["path"] for path in resp.json()]
    assert "/openblade/inbox" in paths
    assert "/openblade/inbox-critical" in paths


def test_sessions_api_includes_upload_audit(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    gateway = protocol_gateway_module.get_gateway()
    gateway.add_credential("audit_user", "pass")
    session = gateway.open_session("audit_user", "10.0.0.1")
    gateway.record_upload(session.session_id, "/openblade/inbox/upload.iso", 2048)
    gateway.close_session(session.session_id)

    resp = client.get("/api/gateway/sessions", headers=admin_auth_headers)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload[0]["uploads"][0]["requested_path"] == "/openblade/inbox/upload.iso"
