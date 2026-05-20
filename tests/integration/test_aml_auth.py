from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'aml-auth.db'}"))
    reset_context(context)
    return TestClient(app)


def _login(client: TestClient, *, name: str = "admin", password: str = "password") -> None:
    response = client.post("/aml/users/login", json={"name": name, "password": password})
    assert response.status_code == 200


@pytest.fixture
def admin_session(client: TestClient) -> dict[str, str | None]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    return {"sessionID": response.cookies.get("sessionID")}


def test_login_success_sets_cookie(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    assert response.cookies.get("sessionID")
    assert client.cookies.get("sessionID")


def test_login_wrong_password_returns_401(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "wrongpass"})
    assert response.status_code == 401



def test_login_nonexistent_user_returns_401(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "missing", "password": "password123"})
    assert response.status_code == 401



def test_public_ldap_enabled_endpoint_requires_no_auth(client: TestClient) -> None:
    response = client.get("/aml/users/ldap/enabled")
    assert response.status_code == 200
    assert response.json() is False



def test_public_lui_access_mode_requires_no_auth(client: TestClient) -> None:
    response = client.get("/aml/users/luiAccess/mode")
    assert response.status_code == 200
    assert response.json() == 2



def test_list_users_requires_auth(client: TestClient) -> None:
    assert client.get("/aml/users").status_code == 401
    _login(client)
    response = client.get("/aml/users")
    assert response.status_code == 200
    users = response.json()["user"]
    assert any(user["name"] == "admin" for user in users)
    assert all("password" not in user for user in users)



def test_get_current_user_returns_authenticated_user(client: TestClient) -> None:
    assert client.get("/aml/users/me").status_code == 401
    _login(client)
    response = client.get("/aml/users/me")
    assert response.status_code == 200
    assert response.json() == {"name": "admin", "role": 0, "requirePasswordChange": True}



def test_create_and_get_user(client: TestClient) -> None:
    _login(client)
    create_response = client.post(
        "/aml/users",
        json={"name": "svc-agent", "password": "StrongPass123!", "role": 2},
    )
    assert create_response.status_code == 201
    assert create_response.json()["role"] == 2
    assert "password" not in create_response.json()

    get_response = client.get("/aml/user/svc-agent")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "svc-agent"
    assert get_response.json()["role"] == 2
    assert "password" not in get_response.json()



def test_logout_clears_session(client: TestClient) -> None:
    _login(client)
    response = client.delete("/aml/users/login")
    assert response.status_code == 200
    assert client.get("/aml/users").status_code == 401


def test_admin_reset_requires_auth(client: TestClient) -> None:
    """POST /aml/users/admin/reset must return 401/403 without a session."""
    resp = client.post("/aml/users/admin/reset", json={"password": "newpass123"})
    assert resp.status_code in (401, 403)


def test_ldap_only_mode_blocks_local_user(client: TestClient, admin_session: dict[str, str | None]) -> None:
    """When login mode=2, local users cannot login."""
    client.put("/aml/users/login/mode", json=2, cookies=admin_session)
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code in (401, 403)
    client.put("/aml/users/login/mode", json=1, cookies=admin_session)


def test_mfa_rejects_invalid_code(client: TestClient, admin_session: dict[str, str | None]) -> None:
    """MFA must reject a bad code when TOTP is enabled for the user."""
    import pyotp

    # Get the TOTP shared secret for the admin user
    key_resp = client.get("/aml/users/login/mfa/totp/key", cookies=admin_session)
    if key_resp.status_code == 404:
        pytest.skip("TOTP key endpoint not available in this build")
    assert key_resp.status_code == 200
    secret = key_resp.json()["key"]

    # Enable MFA using a real valid TOTP code generated from the secret
    valid_code = pyotp.TOTP(secret).now()
    enable_resp = client.put(
        "/aml/users/mfa",
        json={"type": "totp", "enabled": True, "authenticationCode": valid_code},
        cookies=admin_session,
    )
    assert enable_resp.status_code == 200, f"Failed to enable MFA: {enable_resp.text}"

    # Submit a clearly wrong code — must be rejected
    resp = client.post(
        "/aml/users/login/mfa",
        json={"type": "totp", "authenticationCode": "000001"},
        cookies=admin_session,
    )
    assert resp.status_code in (400, 401, 403, 422), f"Expected rejection, got {resp.status_code}: {resp.text}"

    # Disable MFA to clean up (use another valid code)
    cleanup_code = pyotp.TOTP(secret).now()
    client.put(
        "/aml/users/mfa",
        json={"type": "totp", "enabled": False, "authenticationCode": cleanup_code},
        cookies=admin_session,
    )


def test_ldap_get_does_not_leak_password(client: TestClient, admin_session: dict[str, str | None]) -> None:
    """GET /aml/users/ldap must not return searchUserPassword."""
    client.put(
        "/aml/users/ldap",
        json={
            "enabled": False,
            "primaryServer": "ldap.test.com",
            "alternateServer": None,
            "serverPort": 389,
            "secureMode": False,
            "searchUser": "cn=admin,dc=test,dc=com",
            "searchUserPassword": "secret123",
            "usersContext": "ou=users,dc=test,dc=com",
            "groupContext": "ou=groups,dc=test,dc=com",
            "libraryAccessGroupsUser": "users",
            "libraryAccessGroupsAdmin": "admins",
            "realm": None,
            "keyDistributionCenter": None,
            "domainMapping": None,
            "keytabFile": {"name": None, "date": None},
        },
        cookies=admin_session,
    )
    resp = client.get("/aml/users/ldap", cookies=admin_session)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("searchUserPassword") in (None, "", "***")


def test_ldap_put_does_not_leak_password(client: TestClient, admin_session: dict[str, str | None]) -> None:
    """PUT /aml/users/ldap response must also not echo searchUserPassword."""
    ldap_payload = {
        "enabled": False, "primaryServer": "ldap.test.com", "alternateServer": None,
        "serverPort": 389, "secureMode": False, "searchUser": "cn=admin,dc=test,dc=com",
        "searchUserPassword": "topsecret456", "usersContext": "ou=users,dc=test,dc=com",
        "groupContext": "ou=groups,dc=test,dc=com", "libraryAccessGroupsUser": "users",
        "libraryAccessGroupsAdmin": "admins", "realm": None, "keyDistributionCenter": None,
        "domainMapping": None, "keytabFile": {"name": None, "date": None},
    }
    resp = client.put("/aml/users/ldap", json=ldap_payload, cookies=admin_session)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("searchUserPassword") in (None, "", "***"), \
        f"PUT /aml/users/ldap leaked credential: {data.get('searchUserPassword')}"
