"""Helpers for AML matrix endpoint scope enforcement."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_MATRIX_PATH = Path(__file__).resolve().parents[1] / "emulator_contract" / "quantum_i3_rev_h_matrix.json"


def normalize_aml_path(path: str) -> str:
    if not path:
        return "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _compile_template_path(template_path: str) -> re.Pattern[str]:
    segments = [segment for segment in template_path.strip("/").split("/") if segment]
    if not segments:
        return re.compile(r"^/$")
    encoded_segments = [
        r"[^/]+" if segment.startswith("{") and segment.endswith("}") else re.escape(segment)
        for segment in segments
    ]
    return re.compile(rf"^/{'/'.join(encoded_segments)}/?$")


@lru_cache(maxsize=1)
def _load_matrix_endpoint_entries() -> tuple[dict[str, Any], ...]:
    if not _MATRIX_PATH.exists():
        return ()
    try:
        payload = json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ()
    endpoints = payload.get("endpoints")
    if not isinstance(endpoints, list):
        return ()
    cleaned: list[dict[str, Any]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        method = endpoint.get("method")
        path = endpoint.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            continue
        cleaned.append(
            {
                "method": method.upper(),
                "path": path,
                "operation_class": str(endpoint.get("operation_class", "query")),
                "table_id": endpoint.get("table_id"),
            }
        )
    return tuple(cleaned)


@lru_cache(maxsize=1)
def matrix_endpoint_set() -> frozenset[tuple[str, str]]:
    return frozenset((entry["method"], entry["path"]) for entry in _load_matrix_endpoint_entries())


def matrix_endpoint_entries() -> tuple[dict[str, Any], ...]:
    return _load_matrix_endpoint_entries()


@lru_cache(maxsize=1)
def matrix_endpoint_patterns() -> dict[str, tuple[tuple[re.Pattern[str], str], ...]]:
    by_method: dict[str, list[tuple[re.Pattern[str], str]]] = {}
    for entry in _load_matrix_endpoint_entries():
        method = str(entry["method"])
        path_template = str(entry["path"])
        by_method.setdefault(method, []).append((_compile_template_path(path_template), path_template))
    return {method: tuple(items) for method, items in by_method.items()}


def is_matrix_endpoint(method: str, path: str) -> bool:
    normalized_path = normalize_aml_path(path)
    for pattern, _ in matrix_endpoint_patterns().get(method.upper(), ()):
        if pattern.match(normalized_path):
            return True
    return False
