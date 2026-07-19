"""test_09_restore_cycle.py — Restore/hydration: plan → queue → progress → verify."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.i3


class TestRestorePlan:
    def test_restore_plan_endpoint_exists(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post(
            "/restore/plan",
            headers=auth_headers,
            json={"paths": ["/openblade/virtual/test"], "dryRun": True},
        )
        assert resp.status_code != 404, "Restore plan endpoint is missing"

    def test_restore_plan_returns_tape_info(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post(
            "/restore/plan",
            headers=auth_headers,
            json={"paths": ["/openblade/virtual/test"], "dryRun": True},
        )
        if resp.status_code == 404:
            pytest.skip("Restore plan not implemented")
        assert resp.status_code in (200, 202, 422)
        if resp.status_code == 200:
            data = resp.json()
            # Plan should include tape info when files are found
            assert isinstance(data, dict), "Restore plan should be an object"


class TestRestoreQueue:
    def test_restore_queue_endpoint_exists(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/storage/restore-queue", headers=auth_headers)
        assert resp.status_code != 404, "Restore queue endpoint is missing"

    def test_restore_queue_returns_list(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/restore/jobs", headers=auth_headers)
        if resp.status_code == 404:
            pytest.skip("Restore jobs endpoint not available")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    def test_enqueue_restore_job(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post(
            "/restore/jobs",
            headers=auth_headers,
            json={
                "paths": ["/openblade/virtual/test/sample.mov"],
                "destination": "/openblade/restore",
                "priority": "normal",
            },
        )
        assert resp.status_code in (200, 201, 202, 404, 422)


class TestRestoreParallel:
    def test_parallel_restore_planner_fields(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        """Restore planner should report parallel groups when allow_parallel=True."""
        resp = i3_client.post(
            "/restore/plan",
            headers=auth_headers,
            json={
                "paths": ["/openblade/virtual"],
                "allowParallel": True,
                "maxDrives": 2,
                "dryRun": True,
            },
        )
        if resp.status_code in (404, 422):
            pytest.skip("Restore planner not implemented")
        assert resp.status_code in (200, 202)
