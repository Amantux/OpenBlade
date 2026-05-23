from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from openblade.api.rbac_deps import require_permission
from openblade.api.routes_aml_auth import require_auth
from openblade.catalog.db import get_catalog_repository, get_session, init_db
from openblade.catalog.models import AmlUser
from openblade.catalog.repository import CatalogRepository
from openblade.nas import rbac_service as rbac_service_module
from openblade.nas.rbac_service import RbacService
from openblade.nas.types import CreateTokenRequest, CreateUserRequest, RbacPermission


def make_catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def make_service() -> RbacService:
    return RbacService(make_catalog())


def create_role(catalog: CatalogRepository, role_id: str, *permissions: RbacPermission) -> None:
    catalog.create_role(
        {
            "id": role_id,
            "name": role_id,
            "description": role_id,
            "permissions": list(permissions),
        }
    )


def create_user(
    catalog: CatalogRepository,
    *,
    user_id: str,
    username: str,
    role_id: str,
    is_active: bool = True,
    is_admin: bool = False,
) -> None:
    catalog.create_user(
        {
            "id": user_id,
            "username": username,
            "hashed_password": hashlib.sha256(f"{username}-password".encode()).hexdigest(),
            "role_id": role_id,
            "email": f"{username}@example.com",
            "full_name": username.title(),
            "is_active": is_active,
            "is_admin": is_admin,
        }
    )


def create_token(
    catalog: CatalogRepository,
    *,
    token_id: str,
    user_id: str,
    raw_token: str,
    revoked: bool = False,
    expires_at: str | None = None,
) -> None:
    catalog.create_api_token(
        {
            "id": token_id,
            "user_id": user_id,
            "name": token_id,
            "token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
            "permissions": [RbacPermission.TOKEN_MANAGE],
            "expires_at": expires_at,
            "revoked": revoked,
        }
    )


def test_check_permission_granted() -> None:
    service = make_service()
    create_role(service.repo, "operator", RbacPermission.TAPE_READ)
    create_user(service.repo, user_id="user-1", username="alice", role_id="operator")

    assert service.check_permission("user-1", RbacPermission.TAPE_READ) is True


def test_check_permission_denied_wrong_role() -> None:
    service = make_service()
    create_role(service.repo, "readonly", RbacPermission.NAS_READ)
    create_user(service.repo, user_id="user-1", username="alice", role_id="readonly")

    assert service.check_permission("user-1", RbacPermission.TOKEN_MANAGE) is False


def test_check_permission_user_not_found_returns_false() -> None:
    service = make_service()

    assert service.check_permission("missing", RbacPermission.TAPE_READ) is False


def test_require_permission_raises_for_missing_permission() -> None:
    service = make_service()
    create_role(service.repo, "readonly", RbacPermission.NAS_READ)
    create_user(service.repo, user_id="user-1", username="alice", role_id="readonly")

    with pytest.raises(PermissionError, match="Permission denied"):
        service.require_permission("user-1", RbacPermission.USER_ADMIN)


def test_authenticate_by_token_success() -> None:
    service = make_service()
    create_role(service.repo, "operator", RbacPermission.TOKEN_MANAGE)
    create_user(service.repo, user_id="user-1", username="alice", role_id="operator")
    create_token(service.repo, token_id="token-1", user_id="user-1", raw_token="secret-token")

    authenticated = service.authenticate_by_token("secret-token")

    assert authenticated is not None
    assert authenticated.username == "alice"
    stored = service.repo.get_api_token("token-1")
    assert stored is not None
    assert stored["last_used_at"] is not None


def test_authenticate_by_token_revoked_returns_none() -> None:
    service = make_service()
    create_role(service.repo, "operator", RbacPermission.TOKEN_MANAGE)
    create_user(service.repo, user_id="user-1", username="alice", role_id="operator")
    create_token(service.repo, token_id="token-1", user_id="user-1", raw_token="secret-token", revoked=True)

    assert service.authenticate_by_token("secret-token") is None


def test_authenticate_by_token_expired_returns_none() -> None:
    service = make_service()
    create_role(service.repo, "operator", RbacPermission.TOKEN_MANAGE)
    create_user(service.repo, user_id="user-1", username="alice", role_id="operator")
    expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    create_token(service.repo, token_id="token-1", user_id="user-1", raw_token="secret-token", expires_at=expires_at)

    assert service.authenticate_by_token("secret-token") is None


def test_authenticate_by_token_wrong_hash_returns_none() -> None:
    service = make_service()
    create_role(service.repo, "operator", RbacPermission.TOKEN_MANAGE)
    create_user(service.repo, user_id="user-1", username="alice", role_id="operator")
    create_token(service.repo, token_id="token-1", user_id="user-1", raw_token="secret-token")

    assert service.authenticate_by_token("wrong-token") is None


def test_create_user_hashes_password() -> None:
    service = make_service()
    raw_password = "secret-password"
    request = CreateUserRequest(username="alice", password=raw_password, role_id="admin")

    service.create_user(request)

    stored = service.repo.get_user_by_username("alice")
    assert stored is not None
    # Verify password is hashed (PBKDF2) not plaintext
    assert stored["hashed_password"].startswith("pbkdf2$")
    assert stored["hashed_password"] != raw_password
    # Raw password must NOT be stored anywhere in the record
    assert raw_password not in stored.values()
    assert raw_password not in str(stored)


def test_create_user_returns_summary_without_password() -> None:
    service = make_service()

    summary = service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin"))

    assert "hashed_password" not in summary.model_dump()
    assert summary.username == "alice"


def test_create_token_stores_hash_not_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service()
    service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin"))
    monkeypatch.setattr(rbac_service_module.secrets, "token_hex", lambda _: "a" * 64)
    user = service.repo.get_user_by_username("alice")
    assert user is not None

    result = service.create_token(user["id"], CreateTokenRequest(name="cli", permissions=[RbacPermission.TOKEN_MANAGE]))

    stored = service.repo.get_api_token(result.token_id)
    assert stored is not None
    assert stored["token_hash"] == hashlib.sha256(result.raw_token.encode()).hexdigest()
    assert stored["token_hash"] != result.raw_token


def test_create_token_raw_token_different_from_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service()
    service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin"))
    monkeypatch.setattr(rbac_service_module.secrets, "token_hex", lambda _: "b" * 64)
    user = service.repo.get_user_by_username("alice")
    assert user is not None

    result = service.create_token(user["id"], CreateTokenRequest(name="cli", permissions=[RbacPermission.TOKEN_MANAGE]))

    assert result.raw_token != result.token_record.token_hash


def test_revoke_token_marks_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service()
    service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin", is_admin=True))
    monkeypatch.setattr(rbac_service_module.secrets, "token_hex", lambda _: "c" * 64)
    user = service.repo.get_user_by_username("alice")
    assert user is not None
    token = service.create_token(user["id"], CreateTokenRequest(name="cli", permissions=[RbacPermission.TOKEN_MANAGE]))

    assert service.revoke_token(token.token_id, user["id"]) is True
    assert service.repo.get_api_token(token.token_id)["revoked"] is True


def test_list_users_returns_summaries() -> None:
    service = make_service()
    service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin"))
    service.create_user(CreateUserRequest(username="bob", password="another-password", role_id="operator"))

    users = service.list_users()

    assert [user.username for user in users] == ["alice", "bob"]
    assert all("hashed_password" not in user.model_dump() for user in users)


def test_deactivate_user_emits_audit_event() -> None:
    service = make_service()
    service.create_user(CreateUserRequest(username="alice", password="secret-password", role_id="admin", is_admin=True))
    service.create_user(CreateUserRequest(username="bob", password="another-password", role_id="operator"))
    actor = service.repo.get_user_by_username("alice")
    target = service.repo.get_user_by_username("bob")
    assert actor is not None and target is not None

    assert service.deactivate_user(target["id"], actor["id"]) is True
    events = service.repo.list_audit_events(event_type="user_deactivated")
    assert events[0]["details"]["target_user_id"] == target["id"]
    assert service.repo.get_user(target["id"])["is_active"] is False


def test_emit_audit_event_strips_password_fields() -> None:
    service = make_service()

    service.emit_audit_event(
        event_type="test",
        user_id=None,
        username="tester",
        resource="rbac",
        action="create",
        outcome="success",
        details={"password": "secret", "nested": {"hashed_password": "hash", "keep": "ok"}},
    )

    event = service.repo.list_audit_events(limit=1)[0]
    assert "password" not in event["details"]
    assert "hashed_password" not in event["details"]["nested"]
    assert event["details"]["nested"]["keep"] == "ok"


def test_emit_audit_event_strips_token_fields() -> None:
    service = make_service()

    service.emit_audit_event(
        event_type="test",
        user_id=None,
        username="tester",
        resource="rbac",
        action="create",
        outcome="success",
        details={"token": "secret", "items": [{"raw_token": "abc", "value": 1}]},
    )

    event = service.repo.list_audit_events(limit=1)[0]
    assert "token" not in event["details"]
    assert event["details"]["items"] == [{"value": 1}]


def test_require_permission_emits_audit_event_on_denial() -> None:
    repo = make_catalog()
    app = FastAPI()

    @app.get("/protected")
    async def protected(_: AmlUser = Depends(require_permission(RbacPermission.TOKEN_MANAGE))) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_catalog_repository] = lambda: repo
    app.dependency_overrides[require_auth] = lambda: AmlUser(name="guest", password="x", role=1, require_password_change=False)

    client = TestClient(app)
    response = client.get("/protected")

    assert response.status_code == 403
    events = repo.list_audit_events(event_type="permission_denied")
    assert len(events) == 1
    assert events[0]["username"] == "guest"
