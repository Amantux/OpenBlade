"""Integration tests for emulator latency profile settings."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'latency-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


def test_get_emulator_latency_returns_default_profile(authed: TestClient) -> None:
    response = authed.get("/aml/system/emulator/latency")
    assert response.status_code == 200
    payload = response.json()["emulatorLatency"]
    assert payload["profile"] == "instant"
    assert "query" in payload["profileMs"]


def test_put_emulator_latency_requires_auth(client: TestClient) -> None:
    response = client.put("/aml/system/emulator/latency", json={"profile": "realistic"})
    assert response.status_code == 401


def test_put_emulator_latency_rejects_invalid_profile(authed: TestClient) -> None:
    response = authed.put("/aml/system/emulator/latency", json={"profile": "warp"})
    assert response.status_code == 400


def test_put_emulator_latency_updates_profile(authed: TestClient) -> None:
    response = authed.put("/aml/system/emulator/latency", json={"profile": "realistic"})
    assert response.status_code == 200
    assert response.json()["emulatorLatency"]["profile"] == "realistic"

    get_response = authed.get("/aml/system/emulator/latency")
    assert get_response.status_code == 200
    assert get_response.json()["emulatorLatency"]["profile"] == "realistic"


def test_put_emulator_latency_updates_custom_profile_ms(authed: TestClient) -> None:
    response = authed.put(
        "/aml/system/emulator/latency",
        json={
            "profile": "custom",
            "profileMs": {
                "query": {"instant": 0, "realistic": 111, "hardware": 999},
                "move": {"instant": 0, "realistic": 2222, "hardware": 8888},
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()["emulatorLatency"]
    assert payload["profile"] == "custom"
    assert payload["profileMs"]["query"]["realistic"] == 111
    assert payload["profileMs"]["move"]["hardware"] == 8888
