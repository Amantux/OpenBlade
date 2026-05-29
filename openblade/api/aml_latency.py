"""Global AML emulator latency helpers."""

from __future__ import annotations

import asyncio
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request

from openblade.api.aml_state import (
    get_aml_emulator_latency_config,
    record_aml_emulator_latency_metric,
)

_SUPPORTED_PROFILES = {"instant", "realistic", "hardware", "custom"}
_PROFILE_FALLBACK_FOR_CUSTOM = "realistic"
_AML_PATH_PREFIXES = ("/aml", "/iblade")
_LATENCY_METRICS_PATH_PREFIX = "/aml/system/emulator/latency/metrics"
_EMULATOR_MATRIX_PATH = (
    Path(__file__).resolve().parents[1] / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
)


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
def _load_operation_class_patterns() -> dict[str, list[tuple[re.Pattern[str], str]]]:
    by_method: dict[str, list[tuple[re.Pattern[str], str]]] = {}
    if not _EMULATOR_MATRIX_PATH.exists():
        return by_method

    try:
        matrix = json.loads(_EMULATOR_MATRIX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return by_method
    for endpoint in matrix.get("endpoints", []):
        if not isinstance(endpoint, dict):
            continue
        method = endpoint.get("method")
        path = endpoint.get("path")
        operation_class = endpoint.get("operation_class")
        if (
            not isinstance(method, str)
            or not isinstance(path, str)
            or not isinstance(operation_class, str)
        ):
            continue
        compiled = _compile_template_path(path)
        by_method.setdefault(method.upper(), []).append((compiled, operation_class))
    return by_method


def _lookup_operation_class(method: str, path: str) -> str | None:
    candidates = _load_operation_class_patterns().get(method.upper(), [])
    for pattern, operation_class in candidates:
        if pattern.match(path):
            return operation_class
    return None


def _fallback_operation_class(method: str, path: str) -> str:
    lowered_path = path.lower()
    if lowered_path.startswith("/aml/users"):
        return "auth"
    if "/diagnostic" in lowered_path:
        return "diagnostic"
    if "/inventory" in lowered_path:
        return "inventory"
    if "/unmount" in lowered_path:
        return "unmount"
    if "/mount" in lowered_path:
        return "mount"
    if "/move" in lowered_path:
        return "move"
    if "/format" in lowered_path:
        return "format"
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return "config"
    return "query"


def _is_latency_managed_path(path: str) -> bool:
    return path.startswith(_AML_PATH_PREFIXES)


def should_capture_latency_metrics(path: str) -> bool:
    return _is_latency_managed_path(path) and not path.startswith(_LATENCY_METRICS_PATH_PREFIX)


def _effective_profile(profile: str) -> str:
    normalized = profile.strip().lower()
    if normalized not in _SUPPORTED_PROFILES:
        return "instant"
    if normalized == "custom":
        return _PROFILE_FALLBACK_FOR_CUSTOM
    return normalized


def _resolve_profile_delay_ms(
    config: dict[str, Any],
    operation_class: str,
) -> int:
    if not config.get("enabled", True):
        return 0
    profile_ms = config.get("profileMs")
    if not isinstance(profile_ms, dict):
        return 0
    operation_ms = profile_ms.get(operation_class) or profile_ms.get("query")
    if not isinstance(operation_ms, dict):
        return 0
    profile = _effective_profile(str(config.get("profile", "instant")))
    delay_ms = operation_ms.get(profile, 0)
    if not isinstance(delay_ms, int):
        return 0
    return max(0, delay_ms)


def resolve_request_latency_delay_seconds(method: str, path: str) -> float:
    if not _is_latency_managed_path(path):
        return 0.0
    operation_class = _lookup_operation_class(method, path)
    if operation_class is None:
        operation_class = _fallback_operation_class(method, path)
    config = get_aml_emulator_latency_config()
    return _resolve_profile_delay_ms(config, operation_class) / 1000.0


async def apply_request_latency(request: Request) -> float:
    delay = resolve_request_latency_delay_seconds(request.method, request.url.path)
    if delay > 0:
        await asyncio.sleep(delay)
    return delay


def capture_request_latency_metric(
    *,
    method: str,
    endpoint: str,
    status_code: int,
    duration_seconds: float,
    simulated_delay_seconds: float,
) -> None:
    record_aml_emulator_latency_metric(
        method=method,
        endpoint=endpoint,
        status_code=status_code,
        duration_ms=max(0, int(round(duration_seconds * 1000))),
        simulated_delay_ms=max(0, int(round(simulated_delay_seconds * 1000))),
    )
