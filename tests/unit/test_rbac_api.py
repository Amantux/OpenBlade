from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.rbac_service import RbacService
from openblade.nas.types import CreateUserRequest, RbacPermission


def make_client(tmp_path, db_name: str) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / db_name}"))
    reset_context(context)
    return TestClient(app)


def login(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def service_for(client: TestClient) -> RbacService:
    del client
    from openblade.bootstrap import get_context

    return RbacService(get_context().catalog)


def provision_admin_rbac_user(client: TestClient) -> str:
    service = service_for(client)
    existing = service.repo.get_user_by_username("admin")
    if existing is None:
        summary = service.create_user(
            CreateUserRequest(
                username="admin",
                password="rbac-password",
                role_id="admin",
                email="admin@example.com",
                is_admin=True,
            )
        )
        return summary.id
    return str(existing["id"])


def test_list_users_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-1.db")

    response = client.get("/aml/auth/users")

    assert response.status_code == 401


def test_create_user_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-2.db")

    response = client.post("/aml/auth/users", json={"username": "alice", "password": "secret-password", "role_id": "operator"})

    assert response.status_code == 401


def test_list_roles_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-3.db")

    response = client.get("/aml/auth/roles")

    assert response.status_code == 401


def test_list_tokens_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-4.db")

    response = client.get("/aml/auth/tokens")

    assert response.status_code == 401


def test_list_audit_requires_auth(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-5.db")

    response = client.get("/aml/auth/audit")

    assert response.status_code == 401


def test_create_user_endpoint_creates_user(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-6.db")
    login(client)

    response = client.post(
        "/aml/auth/users",
        json={
            "username": "alice",
            "password": "secret-password",
            "role_id": "operator",
            "email": "alice@example.com",
            "full_name": "Alice Example",
        },
    )

    assert response.status_code == 201
    service = service_for(client)
    stored = service.repo.get_user_by_username("alice")
    assert stored is not None
    assert stored["hashed_password"] == hashlib.sha256(b"secret-password").hexdigest()


def test_list_roles_returns_seeded_roles(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-7.db")
    login(client)

    response = client.get("/aml/auth/roles")

    assert response.status_code == 200
    assert [role["name"] for role in response.json()] == ["admin", "operator", "readonly"]


def test_create_token_returns_raw_token_once(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-8.db")
    login(client)
    user_id = provision_admin_rbac_user(client)
    assert user_id

    response = client.post(
        "/aml/auth/tokens",
        json={"name": "cli", "permissions": [RbacPermission.TOKEN_MANAGE.value]},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["raw_token"]
    service = service_for(client)
    tokens = service.repo.list_api_tokens(user_id)
    assert len(tokens) == 1
    assert tokens[0]["token_hash"] != payload["raw_token"]
    listed = client.get("/aml/auth/tokens")
    assert listed.status_code == 200
    assert "raw_token" not in listed.json()[0]


def test_revoke_token_marks_revoked(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-9.db")
    login(client)
    provision_admin_rbac_user(client)

    created = client.post(
        "/aml/auth/tokens",
        json={"name": "cli", "permissions": [RbacPermission.TOKEN_MANAGE.value]},
    )
    assert created.status_code == 201
    token_id = created.json()["token_id"]

    revoked = client.delete(f"/aml/auth/tokens/{token_id}")

    assert revoked.status_code == 200
    service = service_for(client)
    token = service.repo.get_api_token(token_id)
    assert token is not None
    assert token["revoked"] is True


def test_audit_log_endpoint_returns_events(tmp_path) -> None:
    client = make_client(tmp_path, "rbac-api-10.db")
    login(client)
    service = service_for(client)
    service.emit_audit_event(
        event_type="test_event",
        user_id=None,
        username="admin",
        resource="rbac",
        action="read",
        outcome="success",
        details={"source": "test"},
    )

    response = client.get("/aml/auth/audit?event_type=test_event")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "test_event"
    assert payload[0]["details"] == {"source": "test"}
