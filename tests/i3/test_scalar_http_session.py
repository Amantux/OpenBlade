"""Contract tests for ScalarHttpSession against the in-process OpenBlade emulator.

These exercise the real i3 auth dialect (POST /aml/users/login -> session cookie)
without any hardware: a FastAPI TestClient is injected as the httpx transport.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import get_context
from openblade.hardware.scalar_http import ScalarHttpError, ScalarHttpSession


@pytest.fixture
def emulator_client() -> Generator[TestClient, None, None]:
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=True)
    with TestClient(app) as client:
        yield client


def _session(client: TestClient, *, password: str = "password") -> ScalarHttpSession:
    return ScalarHttpSession(client, username="admin", password=password)


def test_scalar_http_session_login_succeeds_against_emulator(emulator_client: TestClient) -> None:
    session = _session(emulator_client)

    session.login()  # must not raise


def test_scalar_http_session_reads_system_status_when_authenticated(
    emulator_client: TestClient,
) -> None:
    session = _session(emulator_client)

    status = session.get_json("/aml/system/status")

    assert isinstance(status, dict)
    assert status  # non-empty payload


def test_scalar_http_session_auto_authenticates_before_first_read(
    emulator_client: TestClient,
) -> None:
    session = _session(emulator_client)

    # No explicit login() call — request() must establish the session first.
    status = session.get_json("/aml/system/status")

    assert isinstance(status, dict)


def test_scalar_http_session_bad_credentials_raise(emulator_client: TestClient) -> None:
    session = _session(emulator_client, password="wrong-password")

    with pytest.raises(ScalarHttpError) as exc_info:
        session.login()

    assert exc_info.value.status_code == 401


def test_scalar_http_session_authenticates_via_cookie_not_bearer(
    emulator_client: TestClient,
) -> None:
    # Real i3 fidelity: auth rides on the sessionID cookie, never an Authorization
    # header. Guards against regressing to the emulator-only Bearer artifact.
    session = _session(emulator_client)
    session.login()

    assert "Authorization" not in session._headers()
    assert "sessionID" in emulator_client.cookies  # cookie jar carries the session
    assert session.get_json("/aml/system/status")  # cookie-authenticated read works
