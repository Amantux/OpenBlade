"""Contract checks for the manual-derived endpoint compliance matrix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.i3


REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
CONTRACT_PATH = REPO_ROOT / "openblade" / "emulator_contract" / "contract.json"
REQUIRED_REQUIREMENT_CATEGORIES = {
    "endpoint",
    "payload-contract",
    "output-contract",
    "state-transition",
    "documented-errors",
    "timing-notes",
}


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


def test_each_endpoint_has_traceable_requirements_for_spec_dimensions() -> None:
    matrix = _load_matrix()
    assert matrix["requirement_id_scheme"] == "{endpoint_id}::{requirement-dimension}"
    seen_requirement_ids: set[str] = set()
    for endpoint in matrix["endpoints"]:
        requirements = endpoint["requirements"]
        assert endpoint["requirement_count"] == len(REQUIRED_REQUIREMENT_CATEGORIES)
        assert len(requirements) == endpoint["requirement_count"]
        categories = {item["category"] for item in requirements}
        assert categories == REQUIRED_REQUIREMENT_CATEGORIES
        for requirement in requirements:
            requirement_id = requirement["id"]
            assert requirement_id.startswith(f"{endpoint['id']}::")
            seen_requirement_ids.add(requirement_id)
    expected_total = matrix["endpoint_count"] * len(REQUIRED_REQUIREMENT_CATEGORIES)
    assert len(seen_requirement_ids) == expected_total


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
        "OPENBLADE_SCALAR_API_ONLY",
    }
    assert required_env.issubset(set(contract["runtime_env"]))


def test_contract_declares_standalone_packaging_assets() -> None:
    contract = _load_contract()
    packaging = contract["standalone_packaging"]
    assert {"compose_file", "runner_script", "runtime_env_example"}.issubset(set(packaging))

    compose_path = REPO_ROOT / packaging["compose_file"]
    runner_path = REPO_ROOT / packaging["runner_script"]
    runtime_env_example_path = REPO_ROOT / packaging["runtime_env_example"]
    assert compose_path.exists(), f"Standalone compose file not found: {compose_path}"
    assert runner_path.exists(), f"Standalone runner script not found: {runner_path}"
    assert runtime_env_example_path.exists(), f"Runtime env template not found: {runtime_env_example_path}"


def test_standalone_compose_matches_contract_defaults_and_boundary() -> None:
    contract = _load_contract()
    packaging = contract["standalone_packaging"]
    compose_path = REPO_ROOT / packaging["compose_file"]
    compose_text = compose_path.read_text(encoding="utf-8")

    assert "dockerfile: deploy/emulator/Dockerfile.local" in compose_text
    assert "OPENBLADE_EMULATOR_LOCAL_IMAGE" in compose_text

    for env_name in contract["runtime_env"]:
        assert f"{env_name}:" in compose_text

    assert "OPENBLADE_SCALAR_API_ONLY: ${OPENBLADE_SCALAR_API_ONLY:-true}" in compose_text
    assert "openblade.api.main:app" not in compose_text


def test_cross_repo_contract_metadata_is_explicit_and_consistent() -> None:
    contract = _load_contract()
    cross_repo = contract["cross_repo_contract"]
    artifacts = cross_repo["required_artifacts"]
    checks = cross_repo["ci_contract_checks"]

    assert contract["contract_schema_version"] == "1.0.0"
    assert cross_repo["contract_line"] == contract["compatibility_policy"]["contract_semver"]
    assert cross_repo["api_compatibility_guarantees"]["manual_scope"] == contract["scope_policy"]
    assert cross_repo["api_compatibility_guarantees"]["matrix_minimum_case_count"] == contract[
        "minimum_cases_per_endpoint"
    ]
    assert {"/health", "/aml/"}.issubset(set(cross_repo["api_compatibility_guarantees"]["required_endpoints"]))

    repo_root = Path(__file__).resolve().parents[2]
    required_paths = [
        artifacts["contract_file"],
        artifacts["manual_matrix_file"],
        artifacts["compose_file"],
        *artifacts["workflow_files"],
    ]
    for relative_path in required_paths:
        assert (repo_root / relative_path).exists(), f"Missing contract artifact path: {relative_path}"

    for check in checks:
        workflow_path = repo_root / check["workflow_file"]
        workflow_text = workflow_path.read_text(encoding="utf-8")
        assert check["command"] in workflow_text
