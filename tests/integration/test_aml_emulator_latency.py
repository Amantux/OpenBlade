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
    assert payload["enabled"] is True
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


def test_put_emulator_latency_updates_enabled_state(authed: TestClient) -> None:
    response = authed.put("/aml/system/emulator/latency", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["emulatorLatency"]["enabled"] is False

    get_response = authed.get("/aml/system/emulator/latency")
    assert get_response.status_code == 200
    assert get_response.json()["emulatorLatency"]["enabled"] is False


def test_latency_profile_is_applied_across_query_and_config_endpoints(
    monkeypatch, authed: TestClient
) -> None:
    sleep_calls: list[float] = []

    async def _capture_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("openblade.api.aml_latency.asyncio.sleep", _capture_sleep)
    response = authed.put(
        "/aml/system/emulator/latency",
        json={
            "enabled": True,
            "profile": "custom",
            "profileMs": {
                "query": {"instant": 0, "realistic": 31, "hardware": 31},
                "config": {"instant": 0, "realistic": 47, "hardware": 47},
            },
        },
    )
    assert response.status_code == 200

    sleep_calls.clear()
    version_response = authed.get("/aml/system/version")
    assert version_response.status_code == 200
    assert sleep_calls == [0.031]

    sleep_calls.clear()
    timezone_response = authed.put("/aml/system/timezone", json={"value": "UTC"})
    assert timezone_response.status_code == 200
    assert sleep_calls == [0.047]

    disable_response = authed.put("/aml/system/emulator/latency", json={"enabled": False})
    assert disable_response.status_code == 200
    sleep_calls.clear()
    version_response = authed.get("/aml/system/version")
    assert version_response.status_code == 200
    assert sleep_calls == []


def test_latency_metrics_capture_and_export_for_aml_and_iblade(authed: TestClient) -> None:
    configure_response = authed.put(
        "/aml/system/emulator/latency",
        json={
            "enabled": True,
            "profile": "custom",
            "profileMs": {
                "query": {"instant": 0, "realistic": 12, "hardware": 12},
                "config": {"instant": 0, "realistic": 17, "hardware": 17},
            },
        },
    )
    assert configure_response.status_code == 200

    reset_response = authed.post("/aml/system/emulator/latency/metrics/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["emulatorLatencyMetrics"]["capturedRequests"] == 0

    version_response = authed.get("/aml/system/version")
    timezone_response = authed.put("/aml/system/timezone", json={"value": "UTC"})
    iblade_response = authed.get("/iblade/states")

    assert version_response.status_code == 200
    assert timezone_response.status_code == 200
    assert iblade_response.status_code == 200

    metrics_response = authed.get("/aml/system/emulator/latency/metrics")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()["emulatorLatencyMetrics"]
    assert metrics["capturedRequests"] == 3
    assert metrics["simulatedDelayMs"]["total"] == 41
    assert metrics["durationMs"]["total"] >= metrics["simulatedDelayMs"]["total"]

    endpoints = {(item["method"], item["endpoint"]): item for item in metrics["endpoints"]}
    assert endpoints[("GET", "/aml/system/version")]["simulatedDelayMs"]["last"] == 12
    assert endpoints[("PUT", "/aml/system/timezone")]["simulatedDelayMs"]["last"] == 17
    assert endpoints[("GET", "/iblade/states")]["simulatedDelayMs"]["last"] == 12

    export_response = authed.get("/aml/system/emulator/latency/metrics/export")
    assert export_response.status_code == 200
    assert export_response.json() == metrics_response.json()


def test_latency_metrics_prometheus_export_requires_auth(client: TestClient) -> None:
    response = client.get("/aml/system/emulator/latency/metrics/prometheus")
    assert response.status_code == 401


def test_latency_metrics_prometheus_export_includes_core_metric_families(
    authed: TestClient,
) -> None:
    reset_response = authed.post("/aml/system/emulator/latency/metrics/reset")
    assert reset_response.status_code == 200

    version_response = authed.get("/aml/system/version")
    iblade_response = authed.get("/iblade/states")
    assert version_response.status_code == 200
    assert iblade_response.status_code == 200

    response = authed.get("/aml/system/emulator/latency/metrics/prometheus")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    payload = response.text
    assert "openblade_system_uptime_seconds" in payload
    assert "openblade_component_status{component=\"network\"}" in payload
    assert "openblade_iblade_request_total{endpoint=\"/iblade/states\",method=\"GET\"" in payload
    assert "openblade_transfer_throughput_files_per_second{operation=\"archive\"}" in payload
    assert "openblade_media_utilization_percent" in payload
    assert "openblade_cleaning_media_total{metric=\"assigned_reports\"}" in payload


def test_latency_metrics_reset_clears_metrics(authed: TestClient) -> None:
    reset_response = authed.post("/aml/system/emulator/latency/metrics/reset")
    assert reset_response.status_code == 200

    version_response = authed.get("/aml/system/version")
    assert version_response.status_code == 200

    metrics_response = authed.get("/aml/system/emulator/latency/metrics")
    assert metrics_response.status_code == 200
    assert metrics_response.json()["emulatorLatencyMetrics"]["capturedRequests"] == 1

    clear_response = authed.post("/aml/system/emulator/latency/metrics/reset")
    assert clear_response.status_code == 200
    cleared = clear_response.json()["emulatorLatencyMetrics"]
    assert cleared["capturedRequests"] == 0
    assert cleared["endpoints"] == []


def test_context_config_can_seed_latency_defaults(tmp_path: Path) -> None:
    context = create_context(
        OpenBladeConfig(
            db_url=f"sqlite:///{tmp_path / 'latency-config-seed.db'}",
            emulator_latency_profile="hardware",
            emulator_latency_enabled=False,
        )
    )
    reset_context(context)
    client = TestClient(app)
    login_response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login_response.status_code == 200

    response = client.get("/aml/system/emulator/latency")
    assert response.status_code == 200
    payload = response.json()["emulatorLatency"]
    assert payload["profile"] == "hardware"
    assert payload["enabled"] is False
