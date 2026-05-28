"""Integration coverage for library-scoped command endpoints."""

from __future__ import annotations

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
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'library-commands.db'}"))
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


def _scoped_headers(base_headers: dict[str, str], library_id: int) -> dict[str, str]:
    return {**base_headers, "X-OpenBlade-Library-Id": str(library_id)}


def test_library_scoped_read_commands_are_available(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    libraries_response = client.get("/api/libraries", headers=admin_auth_headers)
    assert libraries_response.status_code == 200
    libraries = libraries_response.json()
    assert libraries, "Expected seeded libraries for scoped endpoint checks"

    read_command_matrix: list[tuple[str, tuple[int, ...]]] = [
        ("/aml/library", (200, 207)),
        ("/aml/library/inventory", (200,)),
        ("/aml/media", (200,)),
        ("/aml/drives", (200,)),
        ("/aml/summary", (200,)),
    ]

    for library in libraries:
        scoped = _scoped_headers(admin_auth_headers, int(library["id"]))
        for path, expected_statuses in read_command_matrix:
            response = client.get(path, headers=scoped)
            assert response.status_code in expected_statuses, (
                f"{path} failed for library {library['id']}: "
                f"got {response.status_code}, expected {expected_statuses}"
            )


def test_library_scoped_operation_commands_are_wired(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    libraries_response = client.get("/api/libraries", headers=admin_auth_headers)
    assert libraries_response.status_code == 200
    libraries = libraries_response.json()
    assert libraries

    for library in libraries:
        scoped = _scoped_headers(admin_auth_headers, int(library["id"]))

        inventory_scan = client.post("/aml/operations/inventory", headers=scoped, json={})
        assert inventory_scan.status_code in (200, 202, 400, 409, 422)

        move = client.post("/aml/operations/move", headers=scoped, json={})
        assert move.status_code != 404, "Move command endpoint must exist"
        assert move.status_code in (200, 202, 400, 409, 422)

        ie_status = client.get("/aml/library/ie", headers=scoped)
        assert ie_status.status_code in (200, 404)


def test_fleet_and_library_detail_endpoints_support_deep_dive_navigation(
    client: TestClient,
    admin_auth_headers: dict[str, str],
) -> None:
    fleet = client.get("/api/libraries", headers=admin_auth_headers)
    assert fleet.status_code == 200
    libraries = fleet.json()
    assert libraries

    for library in libraries:
        detail = client.get(f"/api/libraries/{library['id']}", headers=admin_auth_headers)
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["id"] == library["id"]
        assert payload["name"] == library["name"]
