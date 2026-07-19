"""test_02_inventory.py — Inventory and physical state validation.

Covers: slot counts, drive states, media list, physical map endpoint.
"""
from __future__ import annotations

import httpx
import pytest

from tests.i3.timing import wait_for_op

pytestmark = pytest.mark.i3


class TestInventory:
    def test_inventory_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200

    def test_inventory_has_slots(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        slots = data.get("slots") or data.get("storageSlots") or []
        assert len(slots) > 0, "Inventory should report at least one slot"

    def test_inventory_has_drives(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data.get("drives") or data.get("tapeDrives") or []
        assert len(drives) > 0, "Inventory should report at least one drive"

    def test_inventory_slot_ids_are_unique(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        slots = data.get("slots") or data.get("storageSlots") or []
        ids = [s.get("slotId") or s.get("id") for s in slots]
        assert len(ids) == len(set(ids)), "Slot IDs must be unique"

    def test_inventory_drive_ids_are_unique(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/inventory", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data.get("drives") or data.get("tapeDrives") or []
        ids = [d.get("driveId") or d.get("id") for d in drives]
        assert len(ids) == len(set(ids)), "Drive IDs must be unique"

    def test_scan_updates_inventory(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        wait_for_op("inventory")
        resp = i3_client.post("/aml/operations/inventory", headers=auth_headers, json={})
        assert resp.status_code in (200, 202), f"Inventory scan failed: {resp.status_code}"


class TestPhysicalMap:
    def test_physical_map_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/physical", headers=auth_headers)
        assert resp.status_code == 200

    def test_physical_map_has_dimensions(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/library/physical", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # Should have some form of geometry/frame info
        assert data, "Physical map response should not be empty"


class TestMediaList:
    def test_media_list_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/media", headers=auth_headers)
        assert resp.status_code == 200

    def test_media_list_entries_have_barcodes(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/media", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        media = data if isinstance(data, list) else data.get("media") or data.get("cartridges") or []
        if media:
            first = media[0]
            barcode = first.get("barcode") or first.get("label") or first.get("volumeLabel")
            assert barcode, f"Media entry missing barcode field: {list(first.keys())}"
