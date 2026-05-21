"""Tests for upload/download API endpoints."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(tmp_path / "restore"))
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'upload-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def test_upload_file_to_pool(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """Upload a small file and get back metadata with checksum."""
    content = b"Hello tape world! " * 100
    response = client.post(
        "/api/pools/1/upload",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "file_id" in data
    assert data["filename"] == "test.txt"
    assert data["size_bytes"] == len(content)
    assert len(data["checksum_sha256"]) == 64
    assert data["status"] == "pending_archive"


def test_upload_checksum_validation(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """Upload with matching checksum should succeed."""
    content = b"checksum test content"
    expected = hashlib.sha256(content).hexdigest()

    response = client.post(
        "/api/pools/1/upload",
        files={"file": ("check.txt", io.BytesIO(content), "text/plain")},
        data={"expected_checksum": expected},
        headers=admin_auth_headers,
    )
    assert response.status_code == 200


def test_upload_checksum_mismatch(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """Upload with wrong checksum should return 400."""
    content = b"mismatch content"
    response = client.post(
        "/api/pools/1/upload",
        files={"file": ("bad.txt", io.BytesIO(content), "text/plain")},
        data={"expected_checksum": "deadbeef" * 8},
        headers=admin_auth_headers,
    )
    assert response.status_code == 400


def test_download_uploaded_file(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """Upload then download should return identical content."""
    content = b"download test " * 50
    upload = client.post(
        "/api/pools/1/upload",
        files={"file": ("download_test.bin", io.BytesIO(content), "application/octet-stream")},
        headers=admin_auth_headers,
    )
    file_id = upload.json()["file_id"]

    response = client.get(f"/api/files/{file_id}/download", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.content == content


def test_download_nonexistent_file(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    # Use a valid UUID4 format that does not correspond to any uploaded file
    response = client.get("/api/files/00000000-0000-4000-8000-000000000000/download", headers=admin_auth_headers)
    assert response.status_code == 404


def test_get_file_checksum(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    content = b"checksum endpoint test"
    upload = client.post(
        "/api/pools/1/upload",
        files={"file": ("chk.txt", io.BytesIO(content), "text/plain")},
        headers=admin_auth_headers,
    )
    file_id = upload.json()["file_id"]

    response = client.get(f"/api/files/{file_id}/checksum", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["checksum_sha256"] == hashlib.sha256(content).hexdigest()


def test_list_pool_files(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    response = client.get("/api/pools/1/files", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "files" in data
    assert isinstance(data["files"], list)


def test_upload_requires_auth(client: TestClient) -> None:
    content = b"no auth"
    response = client.post(
        "/api/pools/1/upload",
        files={"file": ("noauth.txt", io.BytesIO(content), "text/plain")},
    )
    assert response.status_code in (401, 403)


def test_path_traversal_download_rejected(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """file_id with path traversal must be rejected with 400."""
    for evil_id in ["../../etc/passwd", "../staging/somefile", "/etc/passwd", "not-a-uuid"]:
        resp = client.get(f"/api/files/{evil_id}/download", headers=admin_auth_headers)
        assert resp.status_code in (400, 404), f"Expected 400/404 for {evil_id!r}, got {resp.status_code}"


def test_path_traversal_checksum_rejected(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """file_id with path traversal must be rejected with 400 on checksum endpoint."""
    resp = client.get("/api/files/../../etc/shadow/checksum", headers=admin_auth_headers)
    assert resp.status_code in (400, 404)


def test_path_traversal_delete_rejected(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    """file_id with path traversal must be rejected with 400 on delete endpoint."""
    resp = client.delete("/api/files/../../etc/shadow", headers=admin_auth_headers)
    assert resp.status_code in (400, 404)
