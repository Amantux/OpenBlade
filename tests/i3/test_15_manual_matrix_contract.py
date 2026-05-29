"""Contract checks for the manual-derived endpoint compliance matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.i3


MATRIX_PATH = Path(__file__).resolve().parents[2] / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
CONTRACT_PATH = Path(__file__).resolve().parents[2] / "openblade" / "emulator_contract" / "contract.json"


def _load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_manual_matrix_file_exists_and_nonempty() -> None:
    assert MATRIX_PATH.exists(), f"Matrix file not found: {MATRIX_PATH}"
    matrix = _load_matrix()
    assert matrix["endpoint_count"] > 0
    assert len(matrix["endpoints"]) == matrix["endpoint_count"]


def test_each_endpoint_has_minimum_test_templates_and_known_bad_case() -> None:
    matrix = _load_matrix()
    minimum = int(matrix["minimum_cases_per_endpoint"])
    for endpoint in matrix["endpoints"]:
        templates = endpoint["case_templates"]
        assert len(templates) >= minimum
        kinds = {item["kind"] for item in templates}
        assert any(kind.startswith("known-bad") for kind in kinds)


def test_each_endpoint_has_latency_profile_targets() -> None:
    matrix = _load_matrix()
    for endpoint in matrix["endpoints"]:
        latency = endpoint["latency_profile_ms"]
        assert {"instant", "realistic", "hardware"} == set(latency.keys())
        assert 0 <= latency["instant"] <= latency["realistic"] <= latency["hardware"]


def test_manual_endpoint_method_path_keys_are_unique() -> None:
    matrix = _load_matrix()
    keys = [(item["method"], item["path"]) for item in matrix["endpoints"]]
    assert len(keys) == len(set(keys))


def test_matrix_scope_policy_matches_requested_boundary() -> None:
    matrix = _load_matrix()
    assert matrix["scope"] == "manual-documented-apis-only"


def test_emulator_contract_exists_and_matches_matrix_policy() -> None:
    assert CONTRACT_PATH.exists(), f"Contract file not found: {CONTRACT_PATH}"
    contract = _load_contract()
    assert contract["scope_policy"] == "manual-documented-apis-only"
    assert int(contract["minimum_cases_per_endpoint"]) >= 5
    assert contract["manual_matrix_file"].endswith("quantum_i3_rev_h_matrix.json")
    assert set(contract["latency_profiles"]) >= {"instant", "realistic", "hardware"}
    required_env = {
        "LIBRARY_ID",
        "LIBRARY_NAME",
        "EMULATOR_PROFILE",
        "EMULATOR_SLOT_COUNT",
        "EMULATOR_DRIVE_COUNT",
        "EMULATOR_OCCUPANCY_PERCENT",
        "EMULATOR_LATENCY_PROFILE",
    }
    assert required_env.issubset(set(contract["runtime_env"]))
