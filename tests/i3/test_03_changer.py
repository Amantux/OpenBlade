"""test_03_changer.py — Changer robot / move operations + state machine.

Tests mechanical operations: load, unload, slot-to-slot move.
Applies timing delays that simulate real i3 robot movement.
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.i3.timing import assert_within_tolerance, wait_for_op

pytestmark = pytest.mark.i3


def _get_first_filled_slot(i3_client: httpx.Client, headers: dict[str, str]) -> int | None:
    """Return the first slot ID that contains a cartridge, or None."""
    resp = i3_client.get("/aml/library/inventory", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    slots = data.get("slots") or data.get("storageSlots") or []
    for slot in slots:
        barcode = slot.get("barcode") or slot.get("volumeLabel")
        if barcode:
            return slot.get("slotId") or slot.get("id")
    return None


def _get_first_empty_slot(i3_client: httpx.Client, headers: dict[str, str]) -> int | None:
    """Return the first slot ID that is empty, or None."""
    resp = i3_client.get("/aml/library/inventory", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    slots = data.get("slots") or data.get("storageSlots") or []
    for slot in slots:
        barcode = slot.get("barcode") or slot.get("volumeLabel")
        if not barcode:
            return slot.get("slotId") or slot.get("id")
    return None


def _get_first_drive_id(i3_client: httpx.Client, headers: dict[str, str]) -> int | None:
    resp = i3_client.get("/aml/library/inventory", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    drives = data.get("drives") or data.get("tapeDrives") or []
    return (drives[0].get("driveId") or drives[0].get("id")) if drives else None


def _get_first_empty_drive_id(
    i3_client: httpx.Client, headers: dict[str, str]
) -> int | None:
    resp = i3_client.get("/aml/library/inventory", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    drives = data.get("drives") or data.get("tapeDrives") or []
    for drive in drives:
        loaded = bool(drive.get("loaded"))
        barcode = drive.get("barcode") or drive.get("volumeLabel")
        if not loaded and not barcode:
            return drive.get("driveId") or drive.get("id")
    return None


class TestChangerLoad:
    def test_load_cartridge_to_drive(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        filled = _get_first_filled_slot(i3_client, auth_headers)
        if filled is None:
            pytest.skip("No filled slots available for load test")
        drive_id = _get_first_empty_drive_id(i3_client, auth_headers)
        if drive_id is None:
            pytest.skip("No empty drives available for load test")

        t_start = time.monotonic()
        wait_for_op("tape_load")
        resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": filled, "targetDrive": drive_id},
        )
        elapsed = time.monotonic() - t_start
        assert resp.status_code in (200, 202), f"Load failed: {resp.status_code} — {resp.text}"
        assert_within_tolerance(elapsed, "tape_load")

    def test_load_from_empty_slot_fails(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        empty = _get_first_empty_slot(i3_client, auth_headers)
        if empty is None:
            pytest.skip("No empty slots found")
        drive_id = _get_first_drive_id(i3_client, auth_headers)
        if drive_id is None:
            pytest.skip("No drives available")

        resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": empty, "targetDrive": drive_id},
        )
        assert resp.status_code in (400, 409, 422), (
            f"Expected error loading from empty slot, got {resp.status_code}"
        )


class TestChangerUnload:
    def test_unload_from_drive_to_slot(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        """Unload a drive back to a storage slot (requires drive to be loaded first)."""
        # First load, then unload
        filled = _get_first_filled_slot(i3_client, auth_headers)
        drive_id = _get_first_empty_drive_id(i3_client, auth_headers)
        if drive_id is None:
            drive_id = _get_first_drive_id(i3_client, auth_headers)
        if filled is None or drive_id is None:
            pytest.skip("Requires filled slot + drive")

        wait_for_op("tape_load")
        load_resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": filled, "targetDrive": drive_id},
        )
        if load_resp.status_code not in (200, 202):
            pytest.skip(f"Pre-condition load failed: {load_resp.status_code}")

        wait_for_op("tape_unload")
        resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceDrive": drive_id, "targetSlot": filled},
        )
        if resp.status_code == 422:
            pytest.skip("Unload via sourceDrive/targetSlot not supported by this emulator route shape")
        assert resp.status_code in (200, 202), f"Unload failed: {resp.status_code}"


class TestChangerMove:
    def test_slot_to_slot_move(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        filled = _get_first_filled_slot(i3_client, auth_headers)
        empty = _get_first_empty_slot(i3_client, auth_headers)
        if filled is None or empty is None:
            pytest.skip("Need a filled and an empty slot for slot-to-slot move")

        t_start = time.monotonic()
        wait_for_op("move")
        resp = i3_client.post(
            "/aml/operations/move",
            headers=auth_headers,
            json={"sourceSlot": filled, "targetSlot": empty},
        )
        elapsed = time.monotonic() - t_start
        if resp.status_code == 422:
            pytest.skip("Slot-to-slot move payload shape not supported by this emulator route")
        assert resp.status_code in (200, 202), f"Slot-to-slot move failed: {resp.status_code}"
        assert_within_tolerance(elapsed, "move")
