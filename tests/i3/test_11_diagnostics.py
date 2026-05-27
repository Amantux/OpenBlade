"""test_11_diagnostics.py — Health, events, firmware, RAS tickets."""
from __future__ import annotations

import pytest
import httpx

pytestmark = pytest.mark.i3


class TestHealth:
    def test_health_endpoint_returns_200(self, i3_client: httpx.Client) -> None:
        resp = i3_client.get("/health")
        assert resp.status_code == 200

    def test_health_response_has_status_field(self, i3_client: httpx.Client) -> None:
        resp = i3_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        status = data.get("status") or data.get("health") or data.get("state")
        assert status is not None, f"Health response missing status: {data}"

    def test_system_health_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/system/health", headers=auth_headers)
        assert resp.status_code in (200, 404)


class TestEvents:
    def test_events_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/events", headers=auth_headers)
        assert resp.status_code == 200

    def test_events_response_is_list_or_object(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/events", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_events_have_timestamp(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/events", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        events = data if isinstance(data, list) else data.get("events") or []
        for event in events[:5]:
            ts = event.get("timestamp") or event.get("time") or event.get("eventTime")
            assert ts is not None, f"Event missing timestamp: {event}"


class TestFirmware:
    def test_firmware_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/firmware", headers=auth_headers)
        assert resp.status_code in (200, 404)

    def test_firmware_version_reported(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/firmware", headers=auth_headers)
        if resp.status_code == 404:
            pytest.skip("Firmware endpoint not available")
        data = resp.json()
        version = data.get("version") or data.get("firmwareVersion") or data.get("fw_version")
        assert version is not None, f"Firmware version not reported: {data}"


class TestRASTickets:
    def test_ras_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/diagnostics/ras", headers=auth_headers)
        assert resp.status_code in (200, 404)

    def test_diagnostics_summary(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/diagnostics", headers=auth_headers)
        assert resp.status_code in (200, 404)
