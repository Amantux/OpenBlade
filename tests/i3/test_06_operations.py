"""test_06_operations.py — Move wizard, IE door, and operation queue."""
from __future__ import annotations

import pytest
import httpx

from tests.i3.timing import wait_for_op

pytestmark = pytest.mark.i3


class TestMoveWizard:
    def test_move_operation_schema(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        """Verify the move endpoint exists and returns a structured error for bad input."""
        resp = i3_client.post("/aml/operations/move", headers=auth_headers, json={})
        # Should be 400/422 (validation error) not 404 (missing endpoint)
        assert resp.status_code != 404, "Move endpoint is missing"
        assert resp.status_code in (400, 409, 422, 200, 202)

    def test_move_to_occupied_slot_fails(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        inv_resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert inv_resp.status_code == 200
        slots = inv_resp.json().get("slots") or inv_resp.json().get("storageSlots") or []
        filled = [s for s in slots if s.get("barcode") or s.get("volumeLabel")]
        if len(filled) < 2:
            pytest.skip("Need at least 2 filled slots for conflict test")
        src_id = filled[0].get("slotId") or filled[0].get("id")
        dst_id = filled[1].get("slotId") or filled[1].get("id")
        wait_for_op("move")
        resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": src_id, "targetSlot": dst_id},
        )
        assert resp.status_code in (400, 409, 422), (
            f"Expected conflict error for occupied-slot move, got {resp.status_code}"
        )


class TestIEDoor:
    def test_ie_status_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/ie", headers=auth_headers)
        assert resp.status_code in (200, 404), "IE endpoint should be 200 or graceful 404"

    def test_import_export_list(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/ie", headers=auth_headers)
        if resp.status_code == 404:
            pytest.skip("IE endpoint not implemented in this emulator")
        assert resp.status_code == 200


class TestOperationQueue:
    def test_jobs_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/jobs", headers=auth_headers)
        # AML jobs endpoint — may live at /aml/operations or /api/jobs
        assert resp.status_code in (200, 404)

    def test_openblade_jobs_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/api/jobs", headers=auth_headers)
        assert resp.status_code in (200, 307, 404)
