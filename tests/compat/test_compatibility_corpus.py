"""Differential harness for the compatibility corpus (roadmap Phase 0).

Replays each corpus case against the emulator and compares to the recorded
expectation. The point is to stop the emulator being its own oracle:

- `captured` cases (from a real appliance / official spec) are ENFORCED — a
  mismatch fails CI. These are the only cases that prove i3 wire fidelity.
- `inferred` cases (derived from the emulator's own behaviour) are a regression
  guard only; they lock current behaviour but do NOT prove fidelity.

The suite also reports the captured/inferred split so the fidelity gap is loud.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

CORPUS_DIR = Path(__file__).resolve().parents[2] / "compatibility"


def _load_cases() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(CORPUS_DIR.rglob("*.json"))]


CASES = _load_cases()
CAPTURED = [c for c in CASES if c.get("source") == "captured"]
INFERRED = [c for c in CASES if c.get("source") == "inferred"]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'compat.db'}")))
    return TestClient(app)


def _run(client: TestClient, case: dict):
    req = case["request"]
    return client.request(req["method"], req["path"], json=req.get("json"), headers=req.get("headers"))


def _subset_matches(expected: dict, actual: dict) -> list[str]:
    problems = []
    for key, value in expected.items():
        if key not in actual:
            problems.append(f"missing key {key!r}")
        elif actual[key] != value:
            problems.append(f"{key}: expected {value!r}, got {actual[key]!r}")
    return problems


def test_corpus_is_non_empty_and_wellformed() -> None:
    assert CASES, "no compatibility cases found"
    for case in CASES:
        assert case.get("source") in {"captured", "inferred"}, case.get("id")
        assert case.get("profile") and "request" in case and "expected" in case


@pytest.mark.parametrize("case", INFERRED, ids=[c["id"] for c in INFERRED])
def test_inferred_case_behaviour_is_stable(case: dict, client: TestClient) -> None:
    # Regression guard only — NOT proof of appliance fidelity.
    resp = _run(client, case)
    assert resp.status_code == case["expected"]["status"], (
        f"{case['id']}: status {resp.status_code} != {case['expected']['status']}"
    )
    if "json_contains" in case["expected"]:
        problems = _subset_matches(case["expected"]["json_contains"], resp.json())
        assert not problems, f"{case['id']}: {problems}"


@pytest.mark.parametrize("case", CAPTURED or [None], ids=[c["id"] for c in CAPTURED] or ["<none-yet>"])
def test_captured_case_matches_appliance(case, client: TestClient) -> None:
    # Enforced fidelity: the emulator MUST match a real capture exactly.
    if case is None:
        pytest.skip(
            "FIDELITY UNVERIFIED: 0 captured appliance cases in the corpus. "
            "i3 wire fidelity is inferred-only until a real capture is added "
            "(see compatibility/README.md)."
        )
    resp = _run(client, case)
    assert resp.status_code == case["expected"]["status"], f"{case['id']}: status mismatch"
    body = resp.json()
    if "json_equals" in case["expected"]:
        assert body == case["expected"]["json_equals"], f"{case['id']}: body mismatch"
    problems = _subset_matches(case["expected"].get("json_contains", {}), body)
    assert not problems, f"{case['id']}: {problems}"


def test_fidelity_coverage_is_reported(capsys) -> None:
    # Make the gap visible in test output rather than silently green.
    with capsys.disabled():
        print(f"\n[compatibility corpus] captured={len(CAPTURED)} inferred={len(INFERRED)}")
        if not CAPTURED:
            print("[compatibility corpus] WARNING: i3 wire fidelity is UNVERIFIED (no appliance captures).")
    # This assertion documents the current state; flip the expectation when the
    # first real capture lands so a regression to zero captures is caught.
    assert len(CAPTURED) == 0, (
        "Captured cases now exist — update this guard to assert coverage does not regress."
    )
