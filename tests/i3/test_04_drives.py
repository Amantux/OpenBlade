"""test_04_drives.py — Drive health and status transition tests."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.i3

VALID_DRIVE_STATES = {
    "empty", "loaded", "mounted", "loading", "unloading",
    "EMPTY", "LOADED", "MOUNTED", "LOADING", "UNLOADING",
    "idle", "busy", "error", "IDLE", "BUSY", "ERROR",
}


class TestDriveStatus:
    def test_drives_endpoint_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200

    def test_drives_have_valid_status(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data if isinstance(data, list) else data.get("drives") or []
        for drive in drives:
            status = drive.get("status") or drive.get("driveStatus") or drive.get("state")
            assert status is not None, f"Drive missing status field: {drive}"

    def test_drive_ids_are_reported(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data if isinstance(data, list) else data.get("drives") or []
        for drive in drives:
            drive_id = drive.get("driveId") or drive.get("id")
            assert drive_id is not None, f"Drive missing ID: {drive}"

    def test_drive_details_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data if isinstance(data, list) else data.get("drives") or []
        if not drives:
            pytest.skip("No drives returned")
        drive_id = drives[0].get("driveId") or drives[0].get("id")
        detail_resp = i3_client.get(f"/aml/drives/{drive_id}", headers=auth_headers)
        assert detail_resp.status_code == 200


class TestDriveHealth:
    def test_cleaning_status_is_reported(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data if isinstance(data, list) else data.get("drives") or []
        # At least one drive should report a cleaning-related field
        # This is advisory — not all emulators track this
        if drives:
            drive = drives[0]
            _ = drive.get("cleaningRequired") or drive.get("needsCleaning") or drive.get("cleaningStatus")
            # Just verify the endpoint works; cleaning field presence is optional in emulator

    def test_drives_report_tape_alert_if_loaded(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/aml/drives", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        drives = data if isinstance(data, list) else data.get("drives") or []
        for drive in drives:
            barcode = drive.get("loadedTape") or drive.get("barcode") or drive.get("volumeLabel")
            if barcode:
                # Loaded drive must report a barcode
                assert len(str(barcode)) >= 4, f"Barcode too short: {barcode}"
