"""Integration checks for matrix-only AML scope mode."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


def _strict_client(tmp_path: Path) -> TestClient:
    context = create_context(
        OpenBladeConfig(
            db_url=f"sqlite:///{tmp_path / 'scope-lockdown.db'}",
            scalar_api_only=True,
        )
    )
    reset_context(context)
    return TestClient(app)


def test_scope_lockdown_blocks_openblade_api_routes(tmp_path: Path) -> None:
    client = _strict_client(tmp_path)
    response = client.get("/api/libraries")
    assert response.status_code == 404


def test_scope_lockdown_blocks_non_matrix_aml_routes(tmp_path: Path) -> None:
    client = _strict_client(tmp_path)
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login.status_code == 200
    blocked = client.get("/aml/physicalLibrary/magazines")
    assert blocked.status_code == 404


def test_scope_lockdown_allows_matrix_fallback_routes(tmp_path: Path) -> None:
    client = _strict_client(tmp_path)
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login.status_code == 200
    response = client.get("/aml/service/logs")
    assert response.status_code == 200
    assert "logList" in response.json()


def test_scope_lockdown_filters_openapi_to_matrix_paths(tmp_path: Path) -> None:
    client = _strict_client(tmp_path)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json().get("paths", {})
    assert "/api/libraries" not in paths
    assert "/aml/service/logs" in paths


def test_scope_lockdown_openapi_excludes_nas_schemas(tmp_path: Path) -> None:
    client = _strict_client(tmp_path)
    response = client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    schema_text = json.dumps(payload).lower()
    assert "nas" not in schema_text
