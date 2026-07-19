"""Monitoring-trustworthiness gate.

Proves the two things that make alerting trustworthy rather than decorative:
1. the freshness heartbeat metric is actually exported, and
2. every OpenBlade metric referenced by an alert rule is one the exporter really
   knows about — catching the classic "alert watches a metric that was renamed or
   never emitted, so it can never fire" bug. Guards ALL rule files (parity + the
   new operability rules), so it retroactively protects the pre-existing alerts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

PROM_PATH = "/aml/system/emulator/latency/metrics/prometheus"
RULES_DIR = Path(__file__).resolve().parents[2] / "deploy/emulator/observability/prometheus"


@pytest.fixture()
def authed(tmp_path: Path) -> TestClient:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'metrics.db'}")))
    client = TestClient(app)
    assert client.post("/aml/users/login", json={"name": "admin", "password": "password"}).status_code == 200
    return client


def _payload(client: TestClient) -> str:
    resp = client.get(PROM_PATH)
    assert resp.status_code == 200, resp.status_code
    return resp.text


def _known_metric_names(text: str) -> set[str]:
    """Metric names the exporter knows: declared via HELP/TYPE OR emitted as a sample.

    A metric can be declared but currently zero-cardinality (e.g. jobs when the
    queue is empty), so "declared" must count — otherwise the check false-fails.
    """
    names = set(re.findall(r"^# (?:HELP|TYPE) (openblade_[A-Za-z0-9_]+)", text, re.MULTILINE))
    for line in text.splitlines():
        if line and not line.startswith("#"):
            match = re.match(r"(openblade_[A-Za-z0-9_:]+)", line)
            if match:
                names.add(match.group(1))
    return names


def _referenced_openblade_metrics() -> dict[str, set[str]]:
    """{rule_file_name: {openblade_* metric names referenced in any expr}}."""
    refs: dict[str, set[str]] = {}
    rule_files = sorted(RULES_DIR.glob("*.yml"))
    assert rule_files, f"no rule files found under {RULES_DIR}"
    for path in rule_files:
        doc = yaml.safe_load(path.read_text())
        found: set[str] = set()
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                found |= set(re.findall(r"openblade_[A-Za-z0-9_]+", str(rule.get("expr", ""))))
        refs[path.name] = found
    return refs


def test_heartbeat_metric_is_exported_and_fresh(authed: TestClient) -> None:
    text = _payload(authed)
    assert "openblade_metrics_heartbeat_timestamp_seconds" in _known_metric_names(text)
    for line in text.splitlines():
        if line.startswith("openblade_metrics_heartbeat_timestamp_seconds "):
            value = float(line.split()[-1])
            assert value > 1_700_000_000, f"heartbeat is not a plausible unix time: {value}"
            break
    else:
        pytest.fail("heartbeat metric declared but no sample line emitted")


def test_every_alerted_metric_is_actually_exported(authed: TestClient) -> None:
    known = _known_metric_names(_payload(authed))
    referenced = _referenced_openblade_metrics()
    assert any(names for names in referenced.values()), "parsed no metric references from any rule file"
    problems = {name: sorted(metrics - known) for name, metrics in referenced.items() if metrics - known}
    assert not problems, f"alert rules reference metrics the exporter never emits: {problems}"


def test_operability_rules_file_is_valid_and_present() -> None:
    path = RULES_DIR / "openblade-operability-alerts.yml"
    doc = yaml.safe_load(path.read_text())
    alert_names = {r["alert"] for g in doc["groups"] for r in g["rules"]}
    assert {"OpenBladeFleetOffline", "OpenBladeTelemetryStale"} <= alert_names
