"""test_01_auth.py — Authentication tests for the Quantum i3 AML API.

Covers login, session token handling, bad credentials, and logout.
"""
from __future__ import annotations

import pytest
import httpx

from tests.i3.timing import wait_for_op

pytestmark = pytest.mark.i3


class TestLogin:
    def test_login_with_valid_credentials(self, i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> None:
        user, password = i3_credentials
        wait_for_op("auth")
        resp = i3_client.post("/aml/auth/login", json={"username": user, "password": password})
        assert resp.status_code == 200
        data = resp.json()
        token = data.get("token") or data.get("access_token") or data.get("sessionToken")
        assert token, "Login response should contain a token"

    def test_login_with_wrong_password_is_rejected(self, i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> None:
        user, _ = i3_credentials
        resp = i3_client.post("/aml/auth/login", json={"username": user, "password": "wrong-password-xyz"})
        assert resp.status_code in (401, 403), f"Expected 401/403 for bad password, got {resp.status_code}"

    def test_login_with_unknown_user_is_rejected(self, i3_client: httpx.Client) -> None:
        resp = i3_client.post("/aml/auth/login", json={"username": "nonexistent_user_xyz", "password": "any"})
        assert resp.status_code in (401, 403)

    def test_login_response_has_required_fields(self, i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> None:
        user, password = i3_credentials
        resp = i3_client.post("/aml/auth/login", json={"username": user, "password": password})
        assert resp.status_code == 200
        data = resp.json()
        # At least one of these should be present
        has_token = any(k in data for k in ("token", "access_token", "sessionToken"))
        assert has_token, f"Response missing token field: {list(data.keys())}"


class TestAuthenticatedAccess:
    def test_authenticated_request_succeeds(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library", headers=auth_headers)
        assert resp.status_code in (200, 207), f"Authenticated request failed: {resp.status_code}"

    def test_unauthenticated_request_to_protected_endpoint(self, i3_client: httpx.Client) -> None:
        """Endpoints requiring auth should reject requests with no token."""
        # Try a clearly protected mutation endpoint
        resp = i3_client.post("/aml/operations/move", json={})
        assert resp.status_code in (401, 403, 422), (
            f"Expected auth error for unauthenticated request, got {resp.status_code}"
        )


class TestLogout:
    def test_logout_returns_success(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post("/aml/auth/logout", headers=auth_headers)
        assert resp.status_code in (200, 204), f"Logout failed: {resp.status_code}"
