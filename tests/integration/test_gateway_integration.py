"""Integration tests for the protocol gateway management API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas import protocol_gateway as protocol_gateway_module
from openblade.nas.protocol_gateway import ProtocolGateway


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'gateway-integration.db'}"))
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


class TestGatewayLifecycle:
    def test_gateway_full_lifecycle(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Start gateway, add credential, list sessions, stop gateway."""
        r = client.post("/api/gateway/start", headers=admin_auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "running"

        r = client.post(
            "/api/gateway/credentials",
            json={
                "username": "lifecycle_user",
                "password": "lifecycle_pass",
                "allowed_paths": ["/openblade/inbox"],
            },
            headers=admin_auth_headers,
        )
        assert r.status_code == 200

        r = client.get("/api/gateway/credentials", headers=admin_auth_headers)
        assert r.status_code == 200
        assert any(c["username"] == "lifecycle_user" for c in r.json())

        r = client.get("/api/gateway/inbox-paths", headers=admin_auth_headers)
        assert r.status_code == 200
        paths = [p["path"] for p in r.json()]
        assert "/openblade/inbox" in paths
        assert "/openblade/inbox-critical" in paths

        r = client.get("/api/gateway/sessions", headers=admin_auth_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        r = client.post("/api/gateway/stop", headers=admin_auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

    def test_gateway_credential_isolation(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Gateway credentials must not be usable for web API login."""
        create = client.post(
            "/api/gateway/credentials",
            json={
                "username": "sftp_only",
                "password": "sftp_pass",
            },
            headers=admin_auth_headers,
        )
        assert create.status_code == 200

        login = client.post(
            "/aml/users/login",
            json={
                "name": "sftp_only",
                "password": "sftp_pass",
            },
        )
        assert login.status_code in (400, 401, 403, 422)

    def test_gateway_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/gateway/config").status_code in (401, 403)
        assert client.get("/api/gateway/credentials").status_code in (401, 403)
        assert client.post("/api/gateway/start").status_code in (401, 403)
