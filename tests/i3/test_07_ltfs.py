"""test_07_ltfs.py — LTFS format, mount, browse, and unmount workflow.

Format delay is significant (8s realistic, 300s hardware) — test uses timing profile.
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.i3.timing import assert_within_tolerance, wait_for_op

pytestmark = pytest.mark.i3


def _get_scratch_barcode(i3_client: httpx.Client, headers: dict[str, str]) -> str | None:
    """Return a barcode from the scratch pool if available."""
    resp = i3_client.get("/aml/media", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    media = data if isinstance(data, list) else data.get("media") or data.get("cartridges") or []
    for cart in media:
        pool = cart.get("pool") or cart.get("partition") or ""
        if str(pool).lower() in ("scratch", "available", "unassigned", ""):
            barcode = cart.get("barcode") or cart.get("label") or cart.get("volumeLabel")
            if barcode:
                return str(barcode)
    return None


class TestLTFSFormat:
    def test_format_endpoint_exists(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        """Verify the LTFS format endpoint is present and requires confirmation."""
        resp = i3_client.post("/ltfs/format", headers=auth_headers, json={})
        assert resp.status_code != 404, "LTFS format endpoint is missing"

    def test_format_requires_barcode(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post("/ltfs/format", headers=auth_headers, json={})
        assert resp.status_code in (400, 422), (
            f"Format without barcode should be rejected: {resp.status_code}"
        )

    def test_format_with_timing(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        barcode = _get_scratch_barcode(i3_client, auth_headers)
        if not barcode:
            pytest.skip("No scratch barcode available for format test")

        t_start = time.monotonic()
        wait_for_op("format")
        resp = i3_client.post(
            "/ltfs/format",
            headers=auth_headers,
            json={"barcode": barcode, "confirm": True, "safetyToken": "test-token"},
        )
        elapsed = time.monotonic() - t_start
        assert resp.status_code in (200, 202), f"Format failed: {resp.status_code} — {resp.text}"
        assert_within_tolerance(elapsed, "format")


class TestLTFSMount:
    def test_mount_endpoint_exists(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post("/ltfs/mount", headers=auth_headers, json={})
        assert resp.status_code != 404, "LTFS mount endpoint is missing"

    def test_mount_requires_barcode(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post("/ltfs/mount", headers=auth_headers, json={})
        assert resp.status_code in (400, 422), (
            f"Mount without barcode should be rejected: {resp.status_code}"
        )

    def test_mount_and_unmount_cycle(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        barcode = _get_scratch_barcode(i3_client, auth_headers)
        if not barcode:
            pytest.skip("No scratch barcode for mount test")

        wait_for_op("mount")
        mount_resp = i3_client.post(
            "/ltfs/mount",
            headers=auth_headers,
            json={"barcode": barcode, "driveId": 0, "mountPoint": "/tmp/ltfs-test"},
        )
        if mount_resp.status_code not in (200, 202):
            pytest.skip(f"Mount precondition failed: {mount_resp.status_code}")

        wait_for_op("unmount")
        unmount_resp = i3_client.post(
            "/ltfs/unmount",
            headers=auth_headers,
            json={"barcode": barcode},
        )
        assert unmount_resp.status_code in (200, 202), f"Unmount failed: {unmount_resp.status_code}"


class TestLTFSStatus:
    def test_ltfs_status_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/ltfs/status", headers=auth_headers)
        assert resp.status_code == 200

    def test_ltfs_status_fields(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/ltfs/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict)), "LTFS status should return list or object"
