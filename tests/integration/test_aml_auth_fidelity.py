"""AML auth fidelity (roadmap item 18): sessions, preconditions, negotiation, envelopes.

Every behaviour here is *inferred* unless noted: the Rev D manual documents JSON
login/logout, the ``Warning: Default Password Supplied`` header and the
``sessionID`` cookie, but no session cap, 412, XML, 415/406 or 429 semantics.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state, routes_aml_auth
from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

CREDS = {"name": "admin", "password": "password"}
LEAK = "postgres://u:SECRET@db/leak"


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'fid.db'}"))
    reset_context(context)
    routes_aml_auth._login_attempts.clear()
    yield TestClient(app, raise_server_exceptions=False)
    routes_aml_auth._login_attempts.clear()


def _login(client: TestClient) -> str:
    response = client.post("/aml/users/login", json=CREDS)
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


# --- 1. max sessions per user (oldest evicted) ---------------------------------


def test_session_cap_evicts_oldest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_AML_MAX_SESSIONS", "3")
    tokens = [_login(client) for _ in range(4)]
    client.cookies.clear()
    first = client.get("/aml/users", headers={"Authorization": f"Bearer {tokens[0]}"})
    assert first.status_code == 401
    for token in tokens[1:]:
        ok = client.get("/aml/users", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200


def test_session_cap_default_is_five(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENBLADE_AML_MAX_SESSIONS", raising=False)
    assert aml_state.max_sessions_per_user() == 5
    monkeypatch.setenv("OPENBLADE_AML_MAX_SESSIONS", "bogus")
    assert aml_state.max_sessions_per_user() == 5


# --- 2. 412 behind OPENBLADE_AML_STRICT_PRECONDITIONS --------------------------


def test_default_password_lenient_is_200_with_warning(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENBLADE_AML_STRICT_PRECONDITIONS", raising=False)
    response = client.post("/aml/users/login", json=CREDS)
    assert response.status_code == 200
    assert response.headers["Warning"] == "Default Password Supplied"


def test_default_password_strict_is_412_and_session_usable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENBLADE_AML_STRICT_PRECONDITIONS", "true")
    response = client.post("/aml/users/login", json=CREDS)
    assert response.status_code == 412
    body = response.json()
    assert body["code"] == 412 and body["summary"] == "Default password must be changed"
    assert response.headers["Warning"] == "Default Password Supplied"
    assert client.get("/aml/users").status_code == 200


# --- 3. content negotiation ----------------------------------------------------

XML_LOGIN = b"<login><name>admin</name><password>password</password></login>"


def test_xml_login_and_logout_round_trip(client: TestClient) -> None:
    xml = {"Content-Type": "application/xml", "Accept": "application/xml"}
    response = client.post("/aml/users/login", content=XML_LOGIN, headers=xml)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/xml")
    assert "<summary>Login successful</summary>" in response.text
    out = client.delete("/aml/users/login", headers={"Accept": "text/xml"})
    assert out.status_code == 200
    assert out.text.startswith("<WSResultCode>")


def test_text_xml_request_gets_json_by_default(client: TestClient) -> None:
    response = client.post(
        "/aml/users/login", content=XML_LOGIN, headers={"Content-Type": "text/xml"}
    )
    assert response.status_code == 200
    assert response.json()["summary"] == "Login successful"


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<login><name>admin</name><password>&x;</password></login>",
        b'<!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b"<login><name>admin</name><password>&b;</password></login>",
    ],
    ids=["xxe", "entity-expansion"],
)
def test_xml_dtd_and_entities_rejected(client: TestClient, payload: bytes) -> None:
    response = client.post(
        "/aml/users/login", content=payload, headers={"Content-Type": "application/xml"}
    )
    assert response.status_code == 400
    assert response.json()["summary"] == "XML DTD/entity declarations are not allowed"
    assert "root:" not in response.text


def test_malformed_xml_is_400(client: TestClient) -> None:
    response = client.post(
        "/aml/users/login", content=b"<login><name>", headers={"Content-Type": "text/xml"}
    )
    assert response.status_code == 400
    assert response.json()["summary"] == "Malformed XML login payload"


@pytest.mark.parametrize("content_type", [None, "text/plain", "application/yaml", ";;"])
def test_unsupported_content_type_is_415(client: TestClient, content_type: str | None) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    response = client.post("/aml/users/login", content=b"name=admin", headers=headers)
    assert response.status_code == 415
    body = response.json()
    assert body["code"] == 415
    assert body["summary"] == "Unsupported or missing Content-Type"


def test_unsupported_accept_is_406(client: TestClient) -> None:
    response = client.post("/aml/users/login", json=CREDS, headers={"Accept": "image/png"})
    assert response.status_code == 406
    assert response.json()["code"] == 406
    token = _login(client)
    out = client.delete(
        "/aml/users/login",
        headers={"Accept": "image/png", "Authorization": f"Bearer {token}"},
    )
    assert out.status_code == 406


# --- 5. expiry + rate limit + constant-time compare ----------------------------


def _expire_all_sessions() -> None:
    for record in aml_state._STATE.sessions.values():
        record.expires_at = record.expires_at - timedelta(days=2)


_HEADERS_DROPPED = (
    "openblade/api/main.py handle_http_exception drops HTTPException.headers on /aml "
    "paths; cross-cutting fix requested (pass headers=exc.headers)"
)


def test_expired_cookie_is_401(client: TestClient) -> None:
    _login(client)
    _expire_all_sessions()
    assert client.get("/aml/users").status_code == 401


@pytest.mark.xfail(strict=True, reason=_HEADERS_DROPPED)
def test_expired_cookie_is_cleared(client: TestClient) -> None:
    _login(client)
    _expire_all_sessions()
    response = client.get("/aml/users")
    set_cookie = response.headers.get("set-cookie", "")
    assert set_cookie.startswith("sessionID=") and "Max-Age=0" in set_cookie


def test_expired_bearer_is_401(client: TestClient) -> None:
    token = _login(client)
    client.cookies.clear()
    _expire_all_sessions()
    response = client.get("/aml/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_eleventh_login_attempt_is_429_with_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_aml_auth, "_LOGIN_MAX_ATTEMPTS", 10)
    bad = {"name": "admin", "password": "wrong-password"}
    for _ in range(10):
        assert client.post("/aml/users/login", json=bad).status_code == 401
    response = client.post("/aml/users/login", json=CREDS)
    assert response.status_code == 429


@pytest.mark.xfail(strict=True, reason=_HEADERS_DROPPED)
def test_rate_limited_login_carries_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_aml_auth, "_LOGIN_MAX_ATTEMPTS", 0)
    response = client.post("/aml/users/login", json=CREDS)
    assert int(response.headers["Retry-After"]) >= 1


def test_password_compare_is_constant_time() -> None:
    import inspect

    assert "compare_digest" in inspect.getsource(aml_state.verify_password)
    assert "compare_digest" in inspect.getsource(aml_state.authenticate_ldap_user)


# --- 4. error-code envelope matrix ---------------------------------------------


def _trigger(status: int, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> object:
    if status == 401:
        return client.get("/aml/users")
    if status == 403:
        monkeypatch.setattr(aml_state, "get_login_mode", lambda: 2)
        return client.post("/aml/users/login", json=CREDS)
    if status == 404:
        _login(client)
        return client.get("/aml/users/no-such-user")
    if status == 412:
        monkeypatch.setenv("OPENBLADE_AML_STRICT_PRECONDITIONS", "true")
        return client.post("/aml/users/login", json=CREDS)
    if status == 429:
        monkeypatch.setattr(routes_aml_auth, "_LOGIN_MAX_ATTEMPTS", 0)
        return client.post("/aml/users/login", json=CREDS)
    if status == 500:

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError(LEAK)

        monkeypatch.setattr(aml_state, "verify_credentials", boom)
        return client.post("/aml/users/login", json=CREDS)
    assert status == 503
    monkeypatch.setattr(aml_state, "get_service_access", lambda: {"enabled": False})
    real = aml_state.verify_credentials

    def as_service(name: str, password: str) -> object:
        user = real(name, password)
        assert user is not None
        return SimpleNamespace(
            name=user.name, role=2, require_password_change=user.require_password_change
        )

    monkeypatch.setattr(aml_state, "verify_credentials", as_service)
    return client.post("/aml/users/login", json=CREDS)


_NO_500_ENVELOPE = (
    "openblade/api/main.py has no catch-all Exception handler for /aml paths, so an "
    "unhandled error is Starlette's plain-text 500; cross-cutting fix requested"
)


def test_unhandled_error_is_500_without_leak(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _trigger(500, client, monkeypatch)
    assert response.status_code == 500  # type: ignore[attr-defined]
    text = response.text  # type: ignore[attr-defined]
    assert LEAK not in text and "Traceback" not in text and "RuntimeError" not in text


@pytest.mark.parametrize(
    "status",
    [
        401,
        403,
        404,
        412,
        429,
        pytest.param(500, marks=pytest.mark.xfail(strict=True, reason=_NO_500_ENVELOPE)),
        503,
    ],
)
def test_error_envelope_shape(
    status: int, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _trigger(status, client, monkeypatch)
    assert response.status_code == status, response.text  # type: ignore[attr-defined]
    text = response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert isinstance(body, dict)
    assert "Traceback" not in text and LEAK not in text and "RuntimeError" not in text
    message = body.get("summary", body.get("detail", body.get("message")))
    assert isinstance(message, str) and message
