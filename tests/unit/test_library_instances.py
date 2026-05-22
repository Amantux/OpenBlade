"""Tests for multi-library instance CRUD."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'library-instances.db'}"))
    reset_context(context)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    return {}


def test_create_library(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/libraries",
        json={
            "name": "test-lib-1",
            "emulator_url": "http://localhost:8011",
            "model": "Scalar i3",
        },
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-lib-1"
    assert "id" in data


def test_list_libraries(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    response = client.get("/api/libraries", headers=admin_auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    primary = next(item for item in payload if item["name"] == "Primary Tape Library")
    assert primary["role"] == "primary"
    assert primary["sort_order"] == 0


def test_get_library(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/libraries",
        json={"name": "test-lib-2", "emulator_url": "http://localhost:8012"},
        headers=admin_auth_headers,
    )
    lib_id = create.json()["id"]

    response = client.get(f"/api/libraries/{lib_id}", headers=admin_auth_headers)

    assert response.status_code == 200
    assert response.json()["name"] == "test-lib-2"


def test_update_library(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/libraries",
        json={"name": "test-lib-3", "emulator_url": "http://localhost:8013"},
        headers=admin_auth_headers,
    )
    lib_id = create.json()["id"]

    response = client.put(
        f"/api/libraries/{lib_id}",
        json={"enabled": False},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_delete_library(client: TestClient, admin_auth_headers: dict[str, str]) -> None:
    create = client.post(
        "/api/libraries",
        json={"name": "test-lib-delete", "emulator_url": "http://localhost:8099"},
        headers=admin_auth_headers,
    )
    lib_id = create.json()["id"]

    response = client.delete(f"/api/libraries/{lib_id}", headers=admin_auth_headers)
    assert response.status_code == 200

    response = client.get(f"/api/libraries/{lib_id}", headers=admin_auth_headers)
    assert response.status_code == 404


def test_library_requires_auth(client: TestClient) -> None:
    response = client.get("/api/libraries")

    assert response.status_code in (401, 403)
