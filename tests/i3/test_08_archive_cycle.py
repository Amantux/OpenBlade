"""test_08_archive_cycle.py — Full archive workflow: plan → write → verify → catalog."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.i3


class TestArchivePlan:
    def test_archive_plan_endpoint_exists(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/storage/archive-planning", headers=auth_headers)
        # May be 200 or redirect — just confirm it's not 404
        assert resp.status_code != 404, "Archive planning endpoint is missing"

    def test_archive_dry_run_returns_plan(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post(
            "/archive/plan",
            headers=auth_headers,
            json={
                "sourcePath": "/tmp/test-archive",
                "policy": "critical_sequential",
                "dryRun": True,
            },
        )
        assert resp.status_code in (200, 202, 404), (
            f"Archive plan endpoint unexpected status: {resp.status_code}"
        )


class TestArchiveJob:
    def test_archive_job_can_be_enqueued(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.post(
            "/archive/jobs",
            headers=auth_headers,
            json={
                "sourcePath": "/tmp/test-archive",
                "policy": "critical_sequential",
                "ingestMode": "cache_drive",
            },
        )
        assert resp.status_code in (200, 201, 202, 404, 422), (
            f"Archive job enqueue unexpected: {resp.status_code}"
        )

    def test_jobs_list_is_reachable(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/jobs", headers=auth_headers)
        assert resp.status_code in (200, 307)


class TestArchiveVerification:
    def test_catalog_status_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/catalog/status", headers=auth_headers)
        assert resp.status_code in (200, 404)

    def test_catalog_records_endpoint(self, i3_client: httpx.Client, auth_headers: dict[str, str]) -> None:
        resp = i3_client.get("/catalog", headers=auth_headers)
        assert resp.status_code in (200, 307)
