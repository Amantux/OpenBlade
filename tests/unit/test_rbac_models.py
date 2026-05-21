from __future__ import annotations

import hashlib

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import RbacPermission, RbacUserRecord, UserSummary


NOW = "2024-01-01T00:00:00Z"
LATER = "2024-01-02T00:00:00Z"


def make_catalog() -> CatalogRepository:
    init_db("sqlite:///:memory:")
    return CatalogRepository(get_session())


def create_role(catalog: CatalogRepository, *, role_id: str = "role-1", name: str = "operator") -> dict[str, object]:
    return catalog.create_role(
        {
            "id": role_id,
            "name": name,
            "description": f"{name} role",
            "permissions": [RbacPermission.TAPE_READ, RbacPermission.NAS_READ],
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def create_user(catalog: CatalogRepository, *, user_id: str = "user-1", username: str = "alice", is_active: bool = True) -> dict[str, object]:
    return catalog.create_user(
        {
            "id": user_id,
            "username": username,
            "hashed_password": "hashed-password",
            "role_id": "operator",
            "email": f"{username}@example.com",
            "full_name": username.title(),
            "is_active": is_active,
            "is_admin": False,
            "api_token_ids": [],
            "created_at": NOW,
            "updated_at": NOW,
            "last_login_at": None,
        }
    )


def create_api_token(catalog: CatalogRepository, *, token_id: str = "token-1", user_id: str = "user-1", raw_token: str = "secret-token") -> tuple[str, dict[str, object]]:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = catalog.create_api_token(
        {
            "id": token_id,
            "user_id": user_id,
            "name": f"{token_id}-name",
            "token_hash": token_hash,
            "permissions": [RbacPermission.TAPE_READ, RbacPermission.TOKEN_MANAGE],
            "expires_at": None,
            "created_at": NOW,
            "last_used_at": None,
            "revoked": False,
        }
    )
    return raw_token, token


def create_audit_event(catalog: CatalogRepository, *, event_id: str, user_id: str | None, event_type: str, created_at: str) -> dict[str, object]:
    return catalog.create_audit_event(
        {
            "id": event_id,
            "event_type": event_type,
            "user_id": user_id,
            "username": "alice" if user_id else "",
            "resource": "/api/catalog",
            "action": "read",
            "outcome": "allowed",
            "details": {"source": "test"},
            "created_at": created_at,
            "ip_address": "127.0.0.1",
        }
    )


def test_create_role_and_retrieve() -> None:
    catalog = make_catalog()
    created = create_role(catalog)
    fetched = catalog.get_role("role-1")
    assert fetched == created
    assert fetched is not None
    assert fetched["permissions"] == ["tape:read", "nas:read"]


def test_get_role_by_name() -> None:
    catalog = make_catalog()
    create_role(catalog, role_id="role-2", name="readonly")
    fetched = catalog.get_role_by_name("readonly")
    assert fetched is not None
    assert fetched["id"] == "role-2"


def test_list_roles_returns_all() -> None:
    catalog = make_catalog()
    create_role(catalog, role_id="role-1", name="zeta")
    create_role(catalog, role_id="role-2", name="alpha")
    assert [role["name"] for role in catalog.list_roles()] == ["alpha", "zeta"]


def test_update_role_permissions() -> None:
    catalog = make_catalog()
    create_role(catalog)
    updated = catalog.update_role(
        "role-1",
        {
            "permissions": [RbacPermission.TAPE_READ, RbacPermission.TAPE_WRITE, RbacPermission.CATALOG_READ],
            "updated_at": LATER,
        },
    )
    assert updated is not None
    assert updated["permissions"] == ["tape:read", "tape:write", "catalog:read"]
    assert updated["updated_at"] == LATER


def test_delete_role() -> None:
    catalog = make_catalog()
    create_role(catalog)
    assert catalog.delete_role("role-1") is True
    assert catalog.get_role("role-1") is None


def test_create_user_and_retrieve() -> None:
    catalog = make_catalog()
    create_role(catalog)
    created = create_user(catalog)
    fetched = catalog.get_user("user-1")
    assert fetched == created
    assert fetched is not None
    assert fetched["hashed_password"] == "hashed-password"


def test_get_user_by_username() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog, username="bob")
    fetched = catalog.get_user_by_username("bob")
    assert fetched is not None
    assert fetched["username"] == "bob"


def test_list_users_active_only() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog, user_id="user-1", username="alice", is_active=True)
    create_user(catalog, user_id="user-2", username="bob", is_active=False)
    users = catalog.list_users(active_only=True)
    assert [user["username"] for user in users] == ["alice"]


def test_deactivate_user() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    assert catalog.deactivate_user("user-1") is True
    user = catalog.get_user("user-1")
    assert user is not None
    assert user["is_active"] is False


def test_create_api_token_stores_hash_not_raw() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    raw_token, stored = create_api_token(catalog)
    assert stored["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
    assert raw_token not in stored.values()


def test_get_api_token_by_hash() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    raw_token, created = create_api_token(catalog)
    fetched = catalog.get_api_token_by_hash(hashlib.sha256(raw_token.encode()).hexdigest())
    assert fetched == created


def test_revoke_api_token() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    create_api_token(catalog)
    assert catalog.revoke_api_token("token-1") is True
    token = catalog.get_api_token("token-1")
    assert token is not None
    assert token["revoked"] is True


def test_list_api_tokens_for_user() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    create_user(catalog, user_id="user-2", username="bob")
    create_api_token(catalog, token_id="token-1", user_id="user-1", raw_token="one")
    create_api_token(catalog, token_id="token-2", user_id="user-1", raw_token="two")
    create_api_token(catalog, token_id="token-3", user_id="user-2", raw_token="three")
    assert [token["id"] for token in catalog.list_api_tokens("user-1")] == ["token-1", "token-2"]


def test_update_token_last_used() -> None:
    catalog = make_catalog()
    create_role(catalog)
    create_user(catalog)
    create_api_token(catalog)
    catalog.update_token_last_used("token-1", LATER)
    token = catalog.get_api_token("token-1")
    assert token is not None
    assert token["last_used_at"] == LATER


def test_create_audit_event() -> None:
    catalog = make_catalog()
    created = create_audit_event(catalog, event_id="audit-1", user_id="user-1", event_type="login_success", created_at=NOW)
    assert created["details"] == {"source": "test"}
    fetched = catalog.list_audit_events(limit=1)
    assert fetched == [created]


def test_list_audit_events_by_user() -> None:
    catalog = make_catalog()
    create_audit_event(catalog, event_id="audit-1", user_id="user-1", event_type="login_success", created_at=NOW)
    create_audit_event(catalog, event_id="audit-2", user_id="user-2", event_type="login_failure", created_at=LATER)
    events = catalog.list_audit_events(user_id="user-1")
    assert [event["id"] for event in events] == ["audit-1"]


def test_list_audit_events_by_type() -> None:
    catalog = make_catalog()
    create_audit_event(catalog, event_id="audit-1", user_id="user-1", event_type="login_success", created_at=NOW)
    create_audit_event(catalog, event_id="audit-2", user_id="user-1", event_type="token_revoked", created_at=LATER)
    events = catalog.list_audit_events(event_type="token_revoked")
    assert [event["id"] for event in events] == ["audit-2"]


def test_seed_default_roles_creates_three_roles() -> None:
    catalog = make_catalog()
    catalog.seed_default_roles()
    roles = catalog.list_roles()
    assert [role["name"] for role in roles] == ["admin", "operator", "readonly"]
    admin = catalog.get_role_by_name("admin")
    assert admin is not None
    assert len(admin["permissions"]) == len(RbacPermission)


def test_user_summary_omits_hashed_password() -> None:
    user = RbacUserRecord(
        id="user-1",
        username="alice",
        hashed_password="hashed-password",
        role_id="operator",
        email="alice@example.com",
        full_name="Alice",
        is_active=True,
        is_admin=False,
        api_token_ids=[],
        created_at=NOW,
        updated_at=NOW,
        last_login_at=None,
    )
    summary = UserSummary.model_validate(user.model_dump(mode="json"))
    dumped = summary.model_dump(mode="json")
    assert "hashed_password" not in dumped
    assert dumped["username"] == "alice"


def test_rbac_permission_enum_values() -> None:
    assert RbacPermission.TAPE_READ.value == "tape:read"
    assert RbacPermission.NAS_ADMIN.value == "nas:admin"
    assert RbacPermission.SYSTEM_ADMIN.value == "system:admin"
