"""Monitoring-trustworthiness gate.

Proves the things that make alerting trustworthy rather than decorative:
1. the freshness heartbeat metric is actually exported;
2. every OpenBlade metric referenced by an alert rule is one the exporter really
   knows about; and
3. every exact-match label selector in a rule (e.g. `{queue="active"}`) matches an
   emitted series — catching the common "alert watches a metric+label that is
   never produced, so it can never fire" bug, not just a renamed metric name.

To make (3) sound, the fixture seeds representative state (an active job, a
mount) so metrics that are legitimately zero-cardinality on an empty boot
actually emit and their labels can be checked.

Not covered here (verified by convention + docs, not this test): the `up{job=...}`
scrape-target selectors in the fleet/control-plane alerts, which depend on the
operator's prometheus.yml scrape labels and cannot be exercised from in-process.
See docs/monitoring.md for the required label convention.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

PROM_PATH = "/aml/system/emulator/latency/metrics/prometheus"
RULES_DIR = Path(__file__).resolve().parents[2] / "deploy/emulator/observability/prometheus"


@pytest.fixture()
def authed(tmp_path: Path) -> TestClient:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'metrics.db'}")))
    # Seed representative state so data-dependent metrics emit and their label
    # selectors become checkable (jobs by queue/state, an active mount).
    aml_state.set_aml_job("verify-active", {"status": "active", "type": "archive"})
    aml_state.set_aml_mount("verify-mount", {"state": "mounted", "barcode": "VERify01"})
    client = TestClient(app)
    assert client.post("/aml/users/login", json={"name": "admin", "password": "password"}).status_code == 200
    return client


def _payload(client: TestClient) -> str:
    resp = client.get(PROM_PATH)
    assert resp.status_code == 200, resp.status_code
    return resp.text


def _known_metric_names(text: str) -> set[str]:
    """Metric names the exporter knows: declared via HELP/TYPE OR emitted as a sample."""
    names = set(re.findall(r"^# (?:HELP|TYPE) (openblade_[A-Za-z0-9_]+)", text, re.MULTILINE))
    names |= {name for name, _ in _emitted_series(text)}
    return names


def _emitted_series(text: str) -> list[tuple[str, dict[str, str]]]:
    """Parse emitted sample lines into (metric_name, {label: value})."""
    series: list[tuple[str, dict[str, str]]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.match(r"(openblade_[A-Za-z0-9_:]+)(?:\{([^}]*)\})?\s", line)
        if not match:
            continue
        labels = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"', match.group(2) or ""))
        series.append((match.group(1), labels))
    return series


def _referenced_selectors() -> dict[str, list[tuple[str, list[tuple[str, str, str]]]]]:
    """{rule_file: [(metric_name, [(label_key, op, value), ...]), ...]} for openblade_* refs."""
    refs: dict[str, list[tuple[str, list[tuple[str, str, str]]]]] = {}
    rule_files = sorted(RULES_DIR.glob("*.yml"))
    assert rule_files, f"no rule files found under {RULES_DIR}"
    for path in rule_files:
        doc = yaml.safe_load(path.read_text())
        found: list[tuple[str, list[tuple[str, str, str]]]] = []
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                expr = str(rule.get("expr", ""))
                for sel in re.finditer(r"(openblade_[A-Za-z0-9_]+)(?:\{([^}]*)\})?", expr):
                    matchers = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!=|!~|=)\s*"([^"]*)"', sel.group(2) or "")
                    found.append((sel.group(1), matchers))
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


def test_every_alerted_metric_name_is_exported(authed: TestClient) -> None:
    known = _known_metric_names(_payload(authed))
    referenced = {f: {name for name, _ in refs} for f, refs in _referenced_selectors().items()}
    assert any(referenced.values()), "parsed no metric references from any rule file"
    problems = {f: sorted(names - known) for f, names in referenced.items() if names - known}
    assert not problems, f"alert rules reference metrics the exporter never emits: {problems}"


def test_every_alert_label_selector_matches_an_emitted_series(authed: TestClient) -> None:
    text = _payload(authed)
    known = _known_metric_names(text)
    series = _emitted_series(text)
    by_name: dict[str, list[dict[str, str]]] = {}
    for name, labels in series:
        by_name.setdefault(name, []).append(labels)

    problems: list[str] = []
    for fname, refs in _referenced_selectors().items():
        for name, matchers in refs:
            if name not in known:
                continue  # covered by the name test
            samples = by_name.get(name, [])
            if not samples:
                # Zero-cardinality even after seeding: cannot verify labels, but flag
                # exact-match selectors so a never-emitted series can't hide here.
                if any(op == "=" for _, op, _ in matchers):
                    problems.append(f"{fname}: {name} has exact-match labels but emits no series to verify")
                continue
            for key, op, val in matchers:
                if not any(key in labels for labels in samples):
                    problems.append(f"{fname}: {name} is never emitted with label '{key}' — alert cannot match")
                elif op == "=" and not any(labels.get(key) == val for labels in samples):
                    problems.append(f'{fname}: {name}{{{key}="{val}"}} matches no emitted series')
    assert not problems, "untriggerable label selectors: " + "; ".join(problems)


def test_operability_rules_file_is_valid_and_present() -> None:
    doc = yaml.safe_load((RULES_DIR / "openblade-operability-alerts.yml").read_text())
    alert_names = {r["alert"] for g in doc["groups"] for r in g["rules"]}
    assert {"OpenBladeFleetOffline", "OpenBladeTelemetryStale"} <= alert_names
