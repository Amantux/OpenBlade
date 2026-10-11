"""OPENBLADE_EMULATOR_FAULT_PROFILE faults are observable on the AML wire."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api import aml_faults, aml_state
from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str | None) -> TestClient:
    if profile is None:
        monkeypatch.delenv(aml_faults.FAULT_PROFILE_ENV, raising=False)
    else:
        monkeypatch.setenv(aml_faults.FAULT_PROFILE_ENV, profile)
    aml_faults.reset_fault_state()
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'faults.db'}"))
    reset_context(context)
    return TestClient(app)


def _login(client: TestClient) -> None:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200


@pytest.fixture(autouse=True)
def _reset_faults() -> Iterator[None]:
    yield
    aml_faults.reset_fault_state()


def test_no_fault_profile_mount_requests_never_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, None)
    _login(client)

    statuses = [client.post("/aml/operations/mount", json={}).status_code for _ in range(6)]

    assert 409 not in statuses


def test_intermittent_drive_every_third_load_returns_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, "intermittent-drive")
    _login(client)

    responses = [client.post("/aml/operations/mount", json={}) for _ in range(3)]

    assert [r.status_code == 409 for r in responses] == [False, False, True]
    assert responses[2].json()["code"] == "AML_CONFLICT"


def test_rebooting_returns_503_with_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, "rebooting")

    response = client.get("/aml/system")

    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1


def test_session_expiry_profile_expires_session_after_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, '{"auth_ttl_s": 5}')
    start = datetime(2026, 1, 1, 12, 0, 0)
    now = [start]
    monkeypatch.setattr(aml_state, "_utcnow", lambda: now[0])
    _login(client)
    assert client.get("/aml/system").status_code == 200

    now[0] = start + timedelta(seconds=6)
    response = client.get("/aml/system")

    assert response.status_code == 401


def test_no_fault_profile_session_outlives_five_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, None)
    start = datetime(2026, 1, 1, 12, 0, 0)
    now = [start]
    monkeypatch.setattr(aml_state, "_utcnow", lambda: now[0])
    _login(client)

    now[0] = start + timedelta(seconds=6)
    response = client.get("/aml/system")

    assert response.status_code == 200
