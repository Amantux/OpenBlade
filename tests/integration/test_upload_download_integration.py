"""Integration tests for upload/download flows."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import (
    DatasetStatus,
    IngestMode,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(tmp_path / "restore"))
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'upload-download-integration.db'}"))
    reset_context(context)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


class TestUploadRoundTrip:
    def test_small_file_round_trip(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Upload a small file, download it, verify bytes are identical."""
        content = b"Hello tape world! " * 100
        expected_sha = hashlib.sha256(content).hexdigest()

        upload = client.post(
            "/api/pools/integration-pool/upload",
            files={"file": ("small.txt", io.BytesIO(content), "text/plain")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        data = upload.json()
        assert data["checksum_sha256"] == expected_sha
        file_id = data["file_id"]

        download = client.get(f"/api/files/{file_id}/download", headers=admin_auth_headers)
        assert download.status_code == 200
        assert download.content == content

    def test_large_file_round_trip(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Upload a ~512KB file and verify checksum."""
        content = b"X" * (512 * 1024)
        expected_sha = hashlib.sha256(content).hexdigest()

        upload = client.post(
            "/api/pools/integration-pool/upload",
            files={"file": ("large.bin", io.BytesIO(content), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        assert upload.json()["checksum_sha256"] == expected_sha
        assert upload.json()["size_bytes"] == len(content)

    def test_checksum_endpoint_matches_upload(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Checksum endpoint should return same hash as reported at upload time."""
        content = b"checksum endpoint test " * 50
        expected_sha = hashlib.sha256(content).hexdigest()

        upload = client.post(
            "/api/pools/integration-pool/upload",
            files={"file": ("chktest.bin", io.BytesIO(content), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]

        chk = client.get(f"/api/files/{file_id}/checksum", headers=admin_auth_headers)
        assert chk.status_code == 200
        assert chk.json()["checksum_sha256"] == expected_sha

    def test_file_appears_in_pool_listing(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Uploaded file should appear in pool file listing."""
        content = b"pool listing test"
        upload = client.post(
            "/api/pools/listing-pool/upload",
            files={"file": ("listed.txt", io.BytesIO(content), "text/plain")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]

        listing = client.get("/api/pools/listing-pool/files", headers=admin_auth_headers)
        assert listing.status_code == 200
        ids = [f["file_id"] for f in listing.json()["files"]]
        assert file_id in ids

    def test_demo_pool_listing_falls_back_to_archived_records(
        self,
        client: TestClient,
        admin_auth_headers: dict[str, str],
    ) -> None:
        """Archive pools should list seeded NAS records even when no staging inbox dataset exists."""
        service = NasService(get_context().catalog)
        service.upsert_pool(NasPool(id="critical-projects", name="Critical Projects"))
        dataset = service.upsert_dataset(
            NasDataset(
                id="dataset-critical",
                pool_id="critical-projects",
                name="Critical Projects Dataset",
                source_path="/demo/project-alpha",
                ingest_mode=IngestMode.CACHE_DRIVE,
                status=DatasetStatus.ARCHIVED,
            )
        )
        service.upsert_file_record(
            NasFileRecord(
                id="file-critical",
                dataset_id=dataset.id,
                pool_id="critical-projects",
                relative_path="alpha/design-spec-v3.pdf",
                source_path="/demo/project-alpha/alpha/design-spec-v3.pdf",
                size_bytes=512_000_000,
                checksum_sha256="abc123",
                tape_barcode="VOL001L9",
                status=NasFileState.OFFLINE_ON_TAPE,
            )
        )

        listing = client.get("/api/pools/critical-projects/files", headers=admin_auth_headers)

        assert listing.status_code == 200
        payload = listing.json()
        assert payload["pool_id"] == "critical-projects"
        filenames = {item["filename"] for item in payload["files"]}
        assert "design-spec-v3.pdf" in filenames
        assert any(item["status"] == "archived" for item in payload["files"])

    def test_delete_removes_from_listing(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Deleted file should not appear in listing."""
        content = b"delete me"
        upload = client.post(
            "/api/pools/delete-pool/upload",
            files={"file": ("todelete.txt", io.BytesIO(content), "text/plain")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]

        listing_before = client.get("/api/pools/delete-pool/files", headers=admin_auth_headers)
        assert listing_before.status_code == 200
        assert file_id in [f["file_id"] for f in listing_before.json()["files"]]

        delete = client.delete(f"/api/files/{file_id}", headers=admin_auth_headers)
        assert delete.status_code == 200

        listing_after = client.get("/api/pools/delete-pool/files", headers=admin_auth_headers)
        assert listing_after.status_code == 200
        assert file_id not in [f["file_id"] for f in listing_after.json()["files"]]

    def test_download_after_delete_returns_404(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Downloading a deleted file should return 404."""
        content = b"ephemeral"
        upload = client.post(
            "/api/pools/ephemeral-pool/upload",
            files={"file": ("ephemeral.txt", io.BytesIO(content), "text/plain")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]
        delete = client.delete(f"/api/files/{file_id}", headers=admin_auth_headers)
        assert delete.status_code == 200

        dl = client.get(f"/api/files/{file_id}/download", headers=admin_auth_headers)
        assert dl.status_code == 404

    def test_checksum_mismatch_rejects_upload(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Upload with wrong expected_checksum should be rejected and file cleaned up."""
        content = b"real content"
        resp = client.post(
            "/api/pools/integration-pool/upload",
            files={"file": ("bad.txt", io.BytesIO(content), "text/plain")},
            data={"expected_checksum": "deadbeef" * 8},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["detail"].lower()

    def test_binary_content_preserved(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Binary content with null bytes and high bytes should be preserved exactly."""
        content = bytes(range(256)) * 100

        upload = client.post(
            "/api/pools/binary-pool/upload",
            files={"file": ("binary.bin", io.BytesIO(content), "application/octet-stream")},
            headers=admin_auth_headers,
        )
        assert upload.status_code == 200
        file_id = upload.json()["file_id"]

        dl = client.get(f"/api/files/{file_id}/download", headers=admin_auth_headers)
        assert dl.status_code == 200
        assert dl.content == content


class TestSecurityBoundaries:
    def test_path_traversal_rejected(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        for evil in ["../../etc/passwd", "../staging", "/etc/shadow", "not-a-uuid"]:
            r = client.get(f"/api/files/{evil}/download", headers=admin_auth_headers)
            assert r.status_code in (400, 404), f"Expected rejection for {evil!r}"

    def test_all_upload_download_require_auth(self, client: TestClient) -> None:
        assert client.post(
            "/api/pools/1/upload",
            files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")},
        ).status_code in (401, 403)
        assert client.get("/api/files/00000000-0000-4000-8000-000000000001/download").status_code in (401, 403)
        assert client.get("/api/pools/1/files").status_code in (401, 403)
        assert client.delete("/api/files/00000000-0000-4000-8000-000000000001").status_code in (401, 403)
