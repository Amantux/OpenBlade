"""Ensure all manual matrix endpoints are reachable via native or fallback routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from openblade.api.main import app

pytestmark = pytest.mark.i3

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
CATALOG_PATH = REPO_ROOT / "openblade" / "emulator_contract" / "quantum_i3_endpoint_catalog.json"
FALLBACK_MODULE = "openblade.api.routes_aml_matrix_fallback"


def _matrix_endpoints() -> set[tuple[str, str]]:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    return {(str(item["method"]).upper(), str(item["path"])) for item in matrix["endpoints"]}


def _app_routes() -> dict[tuple[str, str], str]:
    routes: dict[tuple[str, str], str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        module = str(getattr(route.endpoint, "__module__", ""))
        for method in route.methods or set():
            if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                routes[(method, route.path)] = module
    return routes


def test_matrix_endpoint_coverage_has_no_missing_routes() -> None:
    matrix = _matrix_endpoints()
    routes = _app_routes()
    missing = sorted(matrix - set(routes))
    assert missing == []


def test_generated_catalog_exists_and_reports_no_missing() -> None:
    assert CATALOG_PATH.exists(), f"Missing generated catalog: {CATALOG_PATH}"
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert int(catalog["status_counts"]["missing"]) == 0
    statuses = {item["status"] for item in catalog["endpoints"]}
    assert statuses.issubset({"native", "fallback-shim"})


def test_generated_catalog_matches_live_route_statuses() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    live_routes = _app_routes()
    for entry in catalog["endpoints"]:
        key = (str(entry["method"]).upper(), str(entry["path"]))
        route_module = live_routes.get(key)
        assert route_module is not None
        expected_status = "fallback-shim" if route_module == FALLBACK_MODULE else "native"
        assert entry["status"] == expected_status
        assert entry["route_module"] == route_module


def test_fallback_routes_are_explicitly_marked_in_catalog() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    fallback_items = [item for item in catalog["endpoints"] if item["status"] == "fallback-shim"]
    for item in fallback_items:
        assert item["route_module"] == FALLBACK_MODULE
