"""Agent-safety policy tests (Layer 1): statically validate the wiki policy files.

These assert the *safety invariants* of the operational wiki without any live LLM:
the remediation catalog cannot silently authorize a state-changing action, the
failure taxonomy is well-formed and references only real metrics, and the wiki
validator passes. See docs/wiki/testing/agent-safety.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WIKI = ROOT / "docs" / "wiki"
CATALOG = WIKI / "agent" / "remediation-catalog.yaml"
TAXONOMY = WIKI / "operations" / "failure-taxonomy.yaml"
SCHEMA = WIKI / "incidents" / "incident-schema.json"
METRICS_SRC = ROOT / "openblade" / "api" / "routes_aml_system.py"

READONLY_AUTONOMOUS = {"rerun_health_check", "collect_diagnostic_evidence", "synthetic_login_probe"}
REQUIRED_ACTION_FIELDS = {
    "id", "description", "enabled", "approval_required", "risk", "environments",
    "components", "minimum_confidence", "verification", "rollback", "stop_conditions",
}


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_no_autonomous_action_is_state_changing() -> None:
    """The core safety invariant: only read-only diagnostics may be non-approval."""
    catalog = _load(CATALOG)
    for action in catalog["actions"]:
        if action.get("approval_required") is False:
            assert action["id"] in READONLY_AUTONOMOUS, (
                f"{action['id']} is autonomous but not a read-only diagnostic — "
                "state-changing autonomous actions are forbidden"
            )


def test_every_action_defines_scope_rollback_verification() -> None:
    catalog = _load(CATALOG)
    for action in catalog["actions"]:
        missing = REQUIRED_ACTION_FIELDS - action.keys()
        assert not missing, f"{action.get('id')} missing policy fields {sorted(missing)}"
        assert action["verification"], f"{action['id']} has no verification steps"
        assert "rollback" in action, f"{action['id']} has no rollback"


def test_denylist_covers_destructive_intents() -> None:
    catalog = _load(CATALOG)
    denylist = set(catalog["denylist"])
    for required in (
        "format_or_erase_tape", "enable_real_hardware", "delete_or_modify_catalog_records",
        "rotate_or_read_secrets", "any_action_not_listed_in_actions",
    ):
        assert required in denylist, f"denylist missing {required}"


def test_kill_switch_present() -> None:
    catalog = _load(CATALOG)
    assert catalog["global"]["kill_switch"]["env"] == "OPENBLADE_AGENT_WRITE_ENABLED"


def test_no_action_permits_real_environment_writes() -> None:
    catalog = _load(CATALOG)
    for action in catalog["actions"]:
        if action.get("approval_required") is False and "real" in action.get("environments", []):
            # a read-only diagnostic in real is fine; a write is not
            assert action["id"] in READONLY_AUTONOMOUS


def test_failure_taxonomy_ids_unique_and_metrics_exist() -> None:
    emitted = set(re.findall(r"openblade_[a-z0-9_]+", METRICS_SRC.read_text(encoding="utf-8")))
    taxonomy = _load(TAXONOMY)
    seen: set[str] = set()
    for failure in taxonomy["failures"]:
        fid = failure["id"]
        assert re.fullmatch(r"[A-Z]+-\d{3}", fid), f"bad failure id {fid}"
        assert fid not in seen, f"duplicate failure id {fid}"
        seen.add(fid)
        assert failure["detection"]["evidence_required"], f"{fid} lists no required evidence"
        for signal in failure["detection"].get("signals", []) or []:
            if isinstance(signal, dict) and "metric" in signal:
                assert signal["metric"] in emitted, f"{fid} references nonexistent metric {signal['metric']}"


def test_incident_schema_is_valid_json() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["incident_id"]["pattern"]


def test_wiki_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "wiki_validate.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"wiki_validate failed:\n{result.stdout}\n{result.stderr}"
