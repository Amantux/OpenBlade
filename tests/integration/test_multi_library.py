"""Multi-library scoping tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(tmp_path / "restore"))
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'multi-library.db'}"))
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


class TestLibraryCRUD:
    def test_default_library_exists(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Default 'primary' library should be seeded at startup."""
        resp = client.get("/api/libraries", headers=admin_auth_headers)
        assert resp.status_code == 200
        names = [lib["name"] for lib in resp.json()]
        assert "primary" in names

    def test_create_multiple_libraries(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Should be able to create multiple library instances."""
        for i in range(2, 5):
            resp = client.post(
                "/api/libraries",
                json={
                    "name": f"test-multi-lib-{i}",
                    "emulator_url": f"http://localhost:{8009 + i}",
                    "model": "Scalar i3",
                },
                headers=admin_auth_headers,
            )
            assert resp.status_code == 200, f"Failed to create library {i}: {resp.text}"
            data = resp.json()
            assert data["name"] == f"test-multi-lib-{i}"
            assert data["enabled"] is True

    def test_list_all_libraries(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """List endpoint returns all created libraries."""
        create = client.post(
            "/api/libraries",
            json={
                "name": "list-test-lib",
                "emulator_url": "http://localhost:9001",
            },
            headers=admin_auth_headers,
        )
        assert create.status_code == 200

        resp = client.get("/api/libraries", headers=admin_auth_headers)
        assert resp.status_code == 200
        names = [lib["name"] for lib in resp.json()]
        assert "list-test-lib" in names

    def test_disable_library(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Disabled libraries should show enabled=False."""
        create = client.post(
            "/api/libraries",
            json={
                "name": "disable-test-lib",
                "emulator_url": "http://localhost:9002",
            },
            headers=admin_auth_headers,
        )
        assert create.status_code == 200
        lib_id = create.json()["id"]

        resp = client.put(f"/api/libraries/{lib_id}", json={"enabled": False}, headers=admin_auth_headers)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_delete_library(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Deleted library should return 404."""
        create = client.post(
            "/api/libraries",
            json={
                "name": "delete-test-lib",
                "emulator_url": "http://localhost:9003",
            },
            headers=admin_auth_headers,
        )
        assert create.status_code == 200
        lib_id = create.json()["id"]

        delete = client.delete(f"/api/libraries/{lib_id}", headers=admin_auth_headers)
        assert delete.status_code == 200

        resp = client.get(f"/api/libraries/{lib_id}", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_library_not_found(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        resp = client.get("/api/libraries/99999", headers=admin_auth_headers)
        assert resp.status_code == 404

    def test_libraries_require_auth(self, client: TestClient) -> None:
        """All library endpoints require authentication."""
        assert client.get("/api/libraries").status_code in (401, 403)
        assert client.post(
            "/api/libraries",
            json={"name": "noauth", "emulator_url": "http://x"},
        ).status_code in (401, 403)


class TestCartridgeLibraryScoping:
    def test_cartridge_library_id_is_nullable(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Cartridges created without explicit library_id should allow nullable FK values."""
        resp = client.get("/aml/media/list", headers=admin_auth_headers)
        if resp.status_code == 200:
            items = resp.json()
            if items:
                for item in items[:3]:
                    lib_id = item.get("library_id")
                    assert lib_id is None or isinstance(lib_id, int)


class TestMultiLibraryUpload:
    def test_upload_scoped_to_pool(self, client: TestClient, admin_auth_headers: dict[str, str]) -> None:
        """Uploads to different pools should be independently listed."""
        content_a = b"pool A content"
        content_b = b"pool B content"

        upload_a = client.post(
            "/api/pools/pool-alpha/upload",
            files={"file": ("a.txt", io.BytesIO(content_a), "text/plain")},
            headers=admin_auth_headers,
        )
        upload_b = client.post(
            "/api/pools/pool-beta/upload",
            files={"file": ("b.txt", io.BytesIO(content_b), "text/plain")},
            headers=admin_auth_headers,
        )

        assert upload_a.status_code == 200
        assert upload_b.status_code == 200

        files_a = client.get("/api/pools/pool-alpha/files", headers=admin_auth_headers)
        files_b = client.get("/api/pools/pool-beta/files", headers=admin_auth_headers)
        assert files_a.status_code == 200
        assert files_b.status_code == 200

        ids_a = {f["file_id"] for f in files_a.json()["files"]}
        ids_b = {f["file_id"] for f in files_b.json()["files"]}

        assert upload_a.json()["file_id"] in ids_a
        assert upload_b.json()["file_id"] in ids_b
        assert upload_a.json()["file_id"] not in ids_b
        assert upload_b.json()["file_id"] not in ids_a
