"""Generate and validate iBlade parity coverage artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "openblade" / "emulator_contract" / "openblade_iblade_rev_a_parity.json"
COVERAGE_JSON_PATH = ROOT / "openblade" / "emulator_contract" / "openblade_iblade_parity_coverage.json"
COVERAGE_MD_PATH = ROOT / "openblade" / "emulator_contract" / "openblade_iblade_parity_coverage.md"
VALID_STATUSES = {"implemented", "partial", "missing"}


def _load_matrix(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _trace_file_from_hint(hint: str) -> str:
    cleaned = hint.strip()
    if not cleaned:
        return ""
    if "::" in cleaned:
        return cleaned.split("::", 1)[0].strip()
    return cleaned.split(":", 1)[0].strip()


def _trace_coverage(hints: list[str]) -> tuple[int, int]:
    covered = 0
    total = 0
    for hint in hints:
        trace_file = _trace_file_from_hint(str(hint))
        if not trace_file:
            continue
        total += 1
        if (ROOT / trace_file).exists():
            covered += 1
    return covered, total


def _feature_row(feature: dict[str, Any]) -> dict[str, Any]:
    feature_id = str(feature.get("id", ""))
    legacy_id = str(feature.get("legacy_id", ""))
    status = str(feature.get("status", "")).strip().lower()
    impl_hints = [str(item) for item in feature.get("implementation_trace_hints", []) if isinstance(item, str)]
    test_hints = [str(item) for item in feature.get("test_trace_hints", []) if isinstance(item, str)]
    gaps = [str(item) for item in feature.get("gaps", []) if isinstance(item, str)]

    impl_covered, impl_total = _trace_coverage(impl_hints)
    test_covered, test_total = _trace_coverage(test_hints)

    return {
        "id": feature_id,
        "legacy_id": legacy_id,
        "status": status,
        "gap_count": len(gaps),
        "implementation_trace_total": impl_total,
        "implementation_trace_covered": impl_covered,
        "test_trace_total": test_total,
        "test_trace_covered": test_covered,
        "implementation_trace_ok": impl_total > 0 and impl_covered == impl_total,
        "test_trace_ok": test_total > 0 and test_covered == test_total,
    }


def _build_coverage(matrix: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    features = matrix.get("features")
    if not isinstance(features, list):
        return {}, ["matrix.features must be an array"]

    rows: list[dict[str, Any]] = []
    status_counts = {status: 0 for status in sorted(VALID_STATUSES)}
    for entry in features:
        if not isinstance(entry, dict):
            errors.append("matrix.features contains a non-object entry")
            continue
        row = _feature_row(entry)
        status = row["status"]
        if status not in VALID_STATUSES:
            errors.append(f"Feature {row['id']} has invalid status: {status!r}")
            continue
        status_counts[status] += 1
        if status == "implemented":
            if not row["implementation_trace_ok"]:
                errors.append(f"Implemented feature {row['id']} has missing implementation trace files")
            if not row["test_trace_ok"]:
                errors.append(f"Implemented feature {row['id']} has missing test trace files")
            if row["gap_count"] > 0:
                errors.append(f"Implemented feature {row['id']} still has documented gaps")
        rows.append(row)

    coverage = {
        "matrix_file": str(MATRIX_PATH.relative_to(ROOT)),
        "feature_count": len(rows),
        "status_counts": status_counts,
        "features": rows,
    }
    return coverage, errors


def _render_markdown(coverage: dict[str, Any]) -> str:
    lines = [
        "# OpenBlade iBlade Parity Coverage",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Feature count | {coverage['feature_count']} |",
        f"| Implemented | {coverage['status_counts']['implemented']} |",
        f"| Partial | {coverage['status_counts']['partial']} |",
        f"| Missing | {coverage['status_counts']['missing']} |",
        "",
        "| ID | Legacy ID | Status | Gaps | Impl Trace | Test Trace |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in coverage["features"]:
        impl = f"{row['implementation_trace_covered']}/{row['implementation_trace_total']}"
        test = f"{row['test_trace_covered']}/{row['test_trace_total']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["id"]),
                    str(row["legacy_id"]),
                    str(row["status"]),
                    str(row["gap_count"]),
                    impl,
                    test,
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=MATRIX_PATH, help="Path to parity matrix JSON")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow partial/missing features without failing validation",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    matrix = _load_matrix(args.matrix)
    coverage, errors = _build_coverage(matrix)
    if not coverage:
        for error in errors:
            print(f"- {error}")
        return 1

    COVERAGE_JSON_PATH.write_text(json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    COVERAGE_MD_PATH.write_text(_render_markdown(coverage), encoding="utf-8")
    print(
        "Generated iBlade parity coverage artifact: "
        f"{COVERAGE_JSON_PATH.relative_to(ROOT)} "
        f"(implemented={coverage['status_counts']['implemented']}, "
        f"partial={coverage['status_counts']['partial']}, "
        f"missing={coverage['status_counts']['missing']})"
    )

    if not args.allow_partial and (
        coverage["status_counts"]["partial"] > 0 or coverage["status_counts"]["missing"] > 0
    ):
        errors.append(
            "Parity matrix still contains partial/missing features; "
            "pass --allow-partial while parity implementation is in progress."
        )

    if errors:
        print("iBlade parity validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("iBlade parity validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
