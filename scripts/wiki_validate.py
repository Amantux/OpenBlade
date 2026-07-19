#!/usr/bin/env python3
"""Validate the OpenBlade operational wiki for agent-consumability.

Checks (fail = non-zero exit):
  1. Every docs/wiki/**/*.md has front matter with required fields and a valid
     `status` and ISO `last_verified` date.
  2. Failure IDs (failure-taxonomy.yaml) and runbook IDs (runbook front matter)
     are unique.
  3. Metric names referenced by the taxonomy + Prometheus alerts EXIST in the set
     the application actually emits (extracted from the source), so the wiki never
     references a nonexistent metric.
  4. Every runbook `failure_ids` resolves to a real failure ID.
  5. Machine artifacts parse: failure-taxonomy.yaml, remediation-catalog.yaml,
     incident-schema.json.
  6. Remediation catalog invariant: no autonomous (approval_required:false) action
     is state-changing outside the read-only diagnostic allowlist.
  7. Internal markdown links to other wiki files resolve.

Usage: python3 scripts/wiki_validate.py   (from repo root)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "docs" / "wiki"
METRICS_SOURCE = ROOT / "openblade" / "api" / "routes_aml_system.py"
ALERTS = WIKI.parent.parent / "deploy/emulator/observability/prometheus/openblade-parity-alerts.yml"

REQUIRED_FM = {"title", "document_type", "status"}
VALID_STATUS = {
    "verified", "provisionally_verified", "inferred", "proposed",
    "outdated", "deprecated", "failed_validation",
}
READONLY_AUTONOMOUS = {"rerun_health_check", "collect_diagnostic_evidence", "synthetic_login_probe"}

errors: list[str] = []


def _front_matter(text: str) -> dict | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        return {"__parse_error__": str(exc)}
    return data if isinstance(data, dict) else {}


def emitted_metrics() -> set[str]:
    text = METRICS_SOURCE.read_text(encoding="utf-8")
    return set(re.findall(r'openblade_[a-z0-9_]+', text))


def check_markdown(metrics: set[str]) -> tuple[set[str], set[str]]:
    runbook_ids: set[str] = set()
    referenced_failures: set[str] = set()
    for path in sorted(WIKI.rglob("*.md")):
        rel = path.relative_to(ROOT)
        fm = _front_matter(path.read_text(encoding="utf-8"))
        if fm is None:
            errors.append(f"{rel}: missing YAML front matter")
            continue
        if "__parse_error__" in fm:
            errors.append(f"{rel}: front matter YAML error: {fm['__parse_error__']}")
            continue
        missing = REQUIRED_FM - fm.keys()
        if missing:
            errors.append(f"{rel}: front matter missing {sorted(missing)}")
        if fm.get("status") not in VALID_STATUS:
            errors.append(f"{rel}: invalid status {fm.get('status')!r}")
        lv = fm.get("last_verified")
        if lv is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(lv)):
            errors.append(f"{rel}: last_verified not YYYY-MM-DD: {lv!r}")
        # runbook-specific
        if fm.get("document_type") == "runbook":
            rid_match = re.search(r"^# (RB-[A-Z]+-\d+)", path.read_text(encoding="utf-8"), re.M)
            if not rid_match:
                errors.append(f"{rel}: runbook missing '# RB-...' heading id")
            else:
                rid = rid_match.group(1)
                if rid in runbook_ids:
                    errors.append(f"{rel}: duplicate runbook id {rid}")
                runbook_ids.add(rid)
            for fid in fm.get("failure_ids", []) or []:
                referenced_failures.add(str(fid))
        # signal metric references must exist
        for sig in fm.get("signals", []) or []:
            if isinstance(sig, dict) and "metric" in sig and sig["metric"] not in metrics:
                errors.append(f"{rel}: references nonexistent metric {sig['metric']}")
        # internal links resolve
        for link in re.findall(r"\]\((?!https?://|#)([^)]+\.(?:md|ya?ml|json))\)", path.read_text(encoding="utf-8")):
            target = (path.parent / link.split("#")[0]).resolve()
            if not target.exists():
                errors.append(f"{rel}: broken link -> {link}")
    return runbook_ids, referenced_failures


def check_taxonomy(metrics: set[str]) -> set[str]:
    tax_path = WIKI / "operations/failure-taxonomy.yaml"
    data = yaml.safe_load(tax_path.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for failure in data.get("failures", []):
        fid = failure.get("id", "")
        if not re.fullmatch(r"[A-Z]+-\d{3}", fid):
            errors.append(f"failure-taxonomy: bad id {fid!r}")
        if fid in ids:
            errors.append(f"failure-taxonomy: duplicate id {fid}")
        ids.add(fid)
        for sig in (failure.get("detection", {}) or {}).get("signals", []) or []:
            if isinstance(sig, dict) and "metric" in sig and sig["metric"] not in metrics:
                errors.append(f"failure-taxonomy {fid}: nonexistent metric {sig['metric']}")
    return ids


def check_alerts(metrics: set[str]) -> None:
    if not ALERTS.exists():
        errors.append(f"alerts file missing: {ALERTS}")
        return
    text = ALERTS.read_text(encoding="utf-8")
    for metric in set(re.findall(r"openblade_[a-z0-9_]+", text)):
        if metric not in metrics:
            errors.append(f"alerts: references nonexistent metric {metric}")


def check_remediation() -> None:
    data = yaml.safe_load((WIKI / "agent/remediation-catalog.yaml").read_text(encoding="utf-8"))
    for action in data.get("actions", []):
        aid = action.get("id")
        if action.get("approval_required") is False and aid not in READONLY_AUTONOMOUS:
            errors.append(
                f"remediation-catalog: {aid} is autonomous but not in the read-only allowlist "
                f"(state-changing autonomous actions are forbidden until agent-safety tests exist)"
            )


def main() -> int:
    metrics = emitted_metrics()
    if not metrics:
        errors.append("could not extract emitted metrics from source")
    runbook_ids, referenced = check_markdown(metrics)
    failure_ids = check_taxonomy(metrics)
    check_alerts(metrics)
    check_remediation()
    json.loads((WIKI / "incidents/incident-schema.json").read_text(encoding="utf-8"))
    for fid in referenced - failure_ids:
        errors.append(f"runbook references unknown failure id {fid}")

    if errors:
        print(f"WIKI VALIDATION FAILED ({len(errors)} error(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        f"Wiki OK: {len(list(WIKI.rglob('*.md')))} pages, {len(failure_ids)} failures, "
        f"{len(runbook_ids)} runbooks, {len(metrics)} emitted metrics, all references resolve."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
