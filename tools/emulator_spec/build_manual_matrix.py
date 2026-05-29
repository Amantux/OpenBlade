"""Build a machine-readable endpoint matrix from Quantum manual text exports.

Usage:
  python3 tools/emulator_spec/build_manual_matrix.py \
    --input /path/to/quantum_webservices.txt \
    --output openblade/emulator_contract/quantum_i3_rev_h_matrix.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TABLE_LINE_RE = re.compile(
    r"Table\s+(?P<table>\d+):\s+(?P<method>GET|POST|PUT|DELETE|PATCH)\s+(?P<path>[^\n]+)",
    re.IGNORECASE,
)
VALID_PATH_RE = re.compile(r"^/?[A-Za-z0-9._~:/{}-]+$")


@dataclass(frozen=True)
class LatencyProfile:
    instant_ms: int
    realistic_ms: int
    hardware_ms: int


LATENCY_BY_OPERATION: dict[str, LatencyProfile] = {
    "auth": LatencyProfile(instant_ms=0, realistic_ms=100, hardware_ms=500),
    "inventory": LatencyProfile(instant_ms=0, realistic_ms=2000, hardware_ms=45000),
    "mount": LatencyProfile(instant_ms=0, realistic_ms=2000, hardware_ms=15000),
    "unmount": LatencyProfile(instant_ms=0, realistic_ms=1500, hardware_ms=10000),
    "move": LatencyProfile(instant_ms=0, realistic_ms=1500, hardware_ms=8000),
    "format": LatencyProfile(instant_ms=0, realistic_ms=8000, hardware_ms=300000),
    "reboot": LatencyProfile(instant_ms=0, realistic_ms=3000, hardware_ms=60000),
    "power": LatencyProfile(instant_ms=0, realistic_ms=2500, hardware_ms=45000),
    "query": LatencyProfile(instant_ms=0, realistic_ms=150, hardware_ms=1200),
    "config": LatencyProfile(instant_ms=0, realistic_ms=400, hardware_ms=3000),
    "diagnostic": LatencyProfile(instant_ms=0, realistic_ms=1500, hardware_ms=90000),
}


def _normalize_path(raw: str) -> str:
    cleaned = raw.replace("\u00a0", " ")
    cleaned = re.split(r"\s+\.{2,}.*$", cleaned, maxsplit=1)[0]
    cleaned = cleaned.split()[0] if cleaned.split() else cleaned
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.rstrip(".")
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _operation_class(method: str, path: str) -> str:
    lowered = path.lower()
    if "login" in lowered or "logout" in lowered or "/auth" in lowered:
        return "auth"
    if "inventory" in lowered:
        return "inventory"
    if "mount" in lowered:
        return "mount"
    if "unmount" in lowered:
        return "unmount"
    if "move" in lowered:
        return "move"
    if "format" in lowered:
        return "format"
    if "reboot" in lowered:
        return "reboot"
    if "power" in lowered or "shutdown" in lowered:
        return "power"
    if "diagnostic" in lowered or "health" in lowered:
        return "diagnostic"
    if method.upper() == "GET":
        return "query"
    return "config"


def _case_templates(endpoint_id: str, method: str) -> list[dict[str, str]]:
    templates: list[dict[str, str]] = [
        {"id": f"{endpoint_id}-happy", "kind": "happy-path"},
        {"id": f"{endpoint_id}-auth-required", "kind": "known-bad-auth"},
        {"id": f"{endpoint_id}-invalid-params", "kind": "known-bad-params"},
        {"id": f"{endpoint_id}-state-transition", "kind": "state-transition"},
        {"id": f"{endpoint_id}-response-contract", "kind": "response-contract"},
    ]
    if method.upper() != "GET":
        templates[2] = {"id": f"{endpoint_id}-invalid-payload", "kind": "known-bad-payload"}
    return templates


def build_matrix(manual_text: str) -> dict[str, object]:
    seen: set[tuple[str, str]] = set()
    seen_tables: set[int] = set()
    endpoints: list[dict[str, object]] = []
    for match in TABLE_LINE_RE.finditer(manual_text):
        table_id = int(match.group("table"))
        if table_id in seen_tables:
            continue
        method = match.group("method").upper()
        path = _normalize_path(match.group("path"))
        if not path.lower().startswith("/aml/"):
            continue
        if not VALID_PATH_RE.fullmatch(path):
            continue
        key = (method, path)
        if key in seen:
            continue
        seen.add(key)
        seen_tables.add(table_id)
        operation = _operation_class(method, path)
        endpoint_id = f"t{table_id:03d}-{method.lower()}-{path.strip('/').replace('/', '-').replace('{', '').replace('}', '')}"
        latency = LATENCY_BY_OPERATION[operation]
        endpoints.append(
            {
                "id": endpoint_id,
                "table_id": table_id,
                "method": method,
                "path": path,
                "operation_class": operation,
                "latency_profile_ms": {
                    "instant": latency.instant_ms,
                    "realistic": latency.realistic_ms,
                    "hardware": latency.hardware_ms,
                },
                "case_templates": _case_templates(endpoint_id, method),
                "minimum_case_count": 5,
            }
        )
    endpoints.sort(key=lambda item: (int(item["table_id"]), str(item["method"]), str(item["path"])))
    return {
        "manual": {
            "name": "Quantum Scalar i6000, i3 & i6 RESTful Web Services API",
            "revision": "6-68185-01 Rev H",
            "source_policy": "latest-authoritative; older-manuals-reference-only",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint_count": len(endpoints),
        "minimum_cases_per_endpoint": 5,
        "scope": "manual-documented-apis-only",
        "endpoints": endpoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Path to manual text export")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON path")
    args = parser.parse_args()

    manual_text = args.input.read_text(encoding="utf-8", errors="replace")
    matrix = build_matrix(manual_text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"Wrote {matrix['endpoint_count']} endpoints to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
