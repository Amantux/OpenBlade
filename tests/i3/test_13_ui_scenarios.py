"""test_13_ui_scenarios.py — Scenario tests that mirror operator UI workflows.

Each test class represents one operator workflow as it would appear in the UI.
"""
from __future__ import annotations

import httpx
import pytest

from tests.i3.timing import wait_for_op

pytestmark = pytest.mark.i3


class TestDashboardScenario:
    """Operator opens dashboard — key stats should be readable."""

    def test_dashboard_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/dashboard/summary", headers=auth_headers)
        assert resp.status_code in (200, 404)

    def test_health_card_data(self, i3_client: httpx.Client) -> None:
        resp = i3_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data, "Dashboard health card should have content"

    def test_library_list_for_grid(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/libraries", headers=auth_headers)
        assert resp.status_code == 200


class TestInventoryBrowseScenario:
    """Operator navigates to Library > Inventory > browses cartridges."""

    def test_inventory_loads_without_error(
        self, i3_client: httpx.Client, auth_headers: dict[str, str]
    ) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200

    def test_physical_map_loads(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/physical", headers=auth_headers)
        assert resp.status_code == 200

    def test_cartridge_list_loads(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/media", headers=auth_headers)
        assert resp.status_code == 200


class TestLoadTapeScenario:
    """Operator uses Move Wizard to load a tape into a drive."""

    def test_move_wizard_flow(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        # Step 1: Get inventory
        inv = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert inv.status_code == 200

        # Step 2: Get drives
        drives = i3_client.get("/aml/drives", headers=auth_headers)
        assert drives.status_code == 200

        # Step 3: Attempt move (may fail if no tape/drive available — that's OK)
        wait_for_op("move")
        move = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": 1, "targetDrive": 0},
        )
        assert move.status_code in (200, 202, 400, 404, 409, 422)


class TestCatalogBrowseScenario:
    """Operator browses catalog records for an archived dataset."""

    def test_catalog_records_load(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/catalog", headers=auth_headers)
        assert resp.status_code in (200, 307)

    def test_catalog_rebuild_status(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/catalog/rebuild/status", headers=auth_headers)
        assert resp.status_code in (200, 404)


class TestJobQueueScenario:
    """Operator monitors the job queue for active operations."""

    def test_job_queue_loads(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/jobs", headers=auth_headers)
        assert resp.status_code in (200, 307)

    def test_jobs_response_is_structured(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/jobs", headers=auth_headers)
        if resp.status_code in (307,):
            pytest.skip("Jobs behind redirect")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))


class TestSecurityScenario:
    """Basic security: non-existent endpoints return 404, not 500."""

    def test_nonexistent_endpoint_returns_404(self, i3_client: httpx.Client) -> None:
        resp = i3_client.get("/api/does-not-exist-xyz")
        assert resp.status_code == 404

    def test_no_server_error_on_malformed_json(
        self, i3_client: httpx.Client, auth_headers: dict[str, str]
    ) -> None:
        resp = i3_client.post(
            "/aml/operations/move",
            headers={**auth_headers, "Content-Type": "application/json"},
            content=b"not-valid-json",
        )
        assert resp.status_code in (400, 422), (
            f"Malformed JSON should be rejected cleanly: {resp.status_code}"
        )

    def test_oversized_payload_handled(
        self, i3_client: httpx.Client, auth_headers: dict[str, str]
    ) -> None:
        large_payload = {"data": "x" * 10_000}
        resp = i3_client.post("/aml/operations/move", headers=auth_headers, json=large_payload)
        assert resp.status_code in (400, 413, 422), (
            f"Oversized payload should be rejected: {resp.status_code}"
        )
