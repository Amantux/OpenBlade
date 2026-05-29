"""test_14_emulator_profile.py — Deterministic default i3 profile assertions."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.i3


def _drives_from_payload(payload: dict) -> list[dict]:
    return payload.get("drives") or (payload.get("driveList") or {}).get("drive") or []


def _media_from_payload(payload: dict) -> list[dict]:
    return payload.get("media") or (payload.get("mediaList") or {}).get("media") or []


def test_default_admin_account_is_available(i3_client: httpx.Client) -> None:
    response = i3_client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def test_default_inventory_matches_quantum_i3_profile(
    i3_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    inventory_response = i3_client.get("/aml/library/inventory", headers=auth_headers)
    assert inventory_response.status_code == 200
    inventory = inventory_response.json()

    slots = inventory.get("slots") or []
    drives = inventory.get("drives") or []
    occupied_slots = sum(1 for slot in slots if bool(slot.get("occupied")))
    loaded_drives = sum(1 for drive in drives if bool(drive.get("loaded")))

    assert len(slots) == 50
    assert len(drives) == 3
    assert occupied_slots + loaded_drives == 30

    library_response = i3_client.get("/aml/library", headers=auth_headers)
    assert library_response.status_code == 200
    library = library_response.json()["library"]
    assert library["slotsTotal"] == 50
    assert 0 <= library["slotsOccupied"] <= 30
    assert library["slotsEmpty"] == 50 - library["slotsOccupied"]


def test_default_drive_mix_is_two_lto7_and_one_lto8(
    i3_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    response = i3_client.get("/aml/drives", headers=auth_headers)
    assert response.status_code == 200
    drives = _drives_from_payload(response.json())
    assert len(drives) == 3

    drive_type_counts: dict[str, int] = {}
    for drive in drives:
        drive_type = str(drive.get("type"))
        drive_type_counts[drive_type] = drive_type_counts.get(drive_type, 0) + 1

    assert drive_type_counts == {"LTO-7": 2, "LTO-8": 1}


def test_default_media_has_mixed_lto_generations(
    i3_client: httpx.Client,
    auth_headers: dict[str, str],
) -> None:
    response = i3_client.get("/aml/media", headers=auth_headers)
    assert response.status_code == 200
    media = _media_from_payload(response.json())
    assert len(media) == 30

    media_types = {str(item.get("type")) for item in media}
    assert "LTO-7" in media_types
    assert "LTO-8" in media_types
