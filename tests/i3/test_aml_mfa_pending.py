"""MFA-required pending session (emulator only; inferred, not in the Rev D manual).

A login by a user with an enabled MFA record answers the documented login body
plus ``Warning: 199 - "MFA Required"``; until ``POST /aml/users/login/mfa``
succeeds the session is refused (401) everywhere except the MFA challenge routes
and logout. The challenge wire shape must be certified by appliance capture.
"""

from __future__ import annotations

import httpx
import pyotp
import pytest

pytestmark = pytest.mark.i3

MFA_WARNING = '199 - "MFA Required"'


def _bearer(client: httpx.Client, user: str, password: str) -> dict[str, str]:
    resp = client.post("/aml/users/login", json={"name": user, "password": password})
    assert resp.status_code in (200, 412), resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_mfa_pending_session_gated_until_totp(
    i3_mode: str, i3_base_url: str, i3_credentials: tuple[str, str]
) -> None:
    if i3_mode != "emulator":
        pytest.skip("enabling MFA on a real appliance account is out of scope")
    user, password = i3_credentials
    with httpx.Client(base_url=i3_base_url, timeout=30.0) as client:
        setup = _bearer(client, user, password)
        secret = client.get("/aml/users/login/mfa/totp/key", headers=setup).json()["key"]
        totp = pyotp.TOTP(secret)
        enabled = client.put(
            "/aml/users/mfa",
            json={"type": "totp", "enabled": True, "authenticationCode": totp.now()},
            headers=setup,
        )
        assert enabled.status_code == 200, enabled.text
        client.cookies.clear()
        pending: dict[str, str] = {}
        try:
            login = client.post("/aml/users/login", json={"name": user, "password": password})
            assert login.status_code in (200, 412)
            assert MFA_WARNING in login.headers.get_list("Warning")
            pending = {"Authorization": f"Bearer {login.json()['token']}"}
            client.cookies.clear()
            assert client.get("/aml/physicalLibrary/elements", headers=pending).status_code == 401
            ok = client.post(
                "/aml/users/login/mfa",
                json={"type": "totp", "authenticationCode": totp.now()},
                headers=pending,
            )
            assert ok.status_code == 200, ok.text
            assert client.get("/aml/physicalLibrary/elements", headers=pending).status_code == 200
        finally:
            if pending:
                client.post(
                    "/aml/users/login/mfa",
                    json={"type": "totp", "authenticationCode": totp.now()},
                    headers=pending,
                )
            client.cookies.clear()
            client.put(
                "/aml/users/mfa",
                json={"type": "totp", "enabled": False, "authenticationCode": totp.now()},
                headers=pending or setup,
            )
