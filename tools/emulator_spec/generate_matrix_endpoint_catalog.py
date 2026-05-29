"""Generate endpoint-by-endpoint matrix implementation catalog artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from openblade.api.main import app

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
CATALOG_JSON_PATH = ROOT / "openblade" / "emulator_contract" / "quantum_i3_endpoint_catalog.json"
CATALOG_MD_PATH = ROOT / "openblade" / "emulator_contract" / "quantum_i3_endpoint_catalog.md"
FALLBACK_MODULE = "openblade.api.routes_aml_matrix_fallback"


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _route_index() -> dict[tuple[str, str], str]:
    indexed: dict[tuple[str, str], str] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        module = str(getattr(route.endpoint, "__module__", ""))
        for method in route.methods or set():
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                continue
            indexed[(method, route.path)] = module
    return indexed


def _status_for_endpoint(
    key: tuple[str, str],
    route_modules: dict[tuple[str, str], str],
) -> str:
    module = route_modules.get(key)
    if module is None:
        return "missing"
    if module == FALLBACK_MODULE:
        return "fallback-shim"
    return "native"


def _build_catalog() -> dict[str, Any]:
    matrix = _load_matrix()
    route_modules = _route_index()
    endpoints: list[dict[str, Any]] = []
    counts = {"native": 0, "fallback-shim": 0, "missing": 0}

    for entry in matrix["endpoints"]:
        method = str(entry["method"]).upper()
        path = str(entry["path"])
        key = (method, path)
        status = _status_for_endpoint(key, route_modules)
        counts[status] += 1
        endpoints.append(
            {
                "table_id": entry.get("table_id"),
                "id": entry.get("id"),
                "method": method,
                "path": path,
                "operation_class": entry.get("operation_class"),
                "status": status,
                "route_module": route_modules.get(key),
            }
        )

    return {
        "matrix_file": str(MATRIX_PATH.relative_to(ROOT)),
        "endpoint_count": len(endpoints),
        "status_counts": counts,
        "endpoints": endpoints,
    }


def _render_markdown(catalog: dict[str, Any]) -> str:
    lines = [
        "# Quantum i3 API Implementation Catalog",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Endpoint count | {catalog['endpoint_count']} |",
        f"| Native | {catalog['status_counts']['native']} |",
        f"| Fallback shim | {catalog['status_counts']['fallback-shim']} |",
        f"| Missing | {catalog['status_counts']['missing']} |",
        "",
        "| Table | Method | Path | Class | Status | Route module |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for endpoint in catalog["endpoints"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(endpoint.get("table_id", "")),
                    str(endpoint["method"]),
                    str(endpoint["path"]),
                    str(endpoint.get("operation_class", "")),
                    str(endpoint["status"]),
                    str(endpoint.get("route_module", "") or ""),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    catalog = _build_catalog()
    CATALOG_JSON_PATH.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CATALOG_MD_PATH.write_text(_render_markdown(catalog), encoding="utf-8")
    print(
        "Generated endpoint catalog: "
        f"{CATALOG_JSON_PATH.relative_to(ROOT)} "
        f"(native={catalog['status_counts']['native']}, "
        f"fallback={catalog['status_counts']['fallback-shim']}, "
        f"missing={catalog['status_counts']['missing']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
