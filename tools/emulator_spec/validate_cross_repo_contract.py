"""Validate cross-repository emulator compatibility metadata."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "openblade" / "emulator_contract" / "contract.json"
MATRIX_PATH = ROOT / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"
COMPOSE_PATH = ROOT / "docker-compose.yml"
IMAGE_PIN_RE = re.compile(r"image:\s+\$\{OPENBLADE_EMULATOR_IMAGE:-(?P<image>[^}]+)\}")


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_dict(parent: dict[str, object], key: str, errors: list[str]) -> dict[str, object]:
    value = parent.get(key)
    if isinstance(value, dict):
        return value
    errors.append(f"{key} must be an object")
    return {}


def _require_list(parent: dict[str, object], key: str, errors: list[str]) -> list[object]:
    value = parent.get(key)
    if isinstance(value, list):
        return value
    errors.append(f"{key} must be an array")
    return []


def _validate() -> list[str]:
    errors: list[str] = []
    contract = _load_json(CONTRACT_PATH)
    matrix = _load_json(MATRIX_PATH)

    version = str(contract.get("version", ""))
    image = _require_dict(contract, "image", errors)
    compatibility_policy = _require_dict(contract, "compatibility_policy", errors)
    cross_repo = _require_dict(contract, "cross_repo_contract", errors)
    artifacts = _require_dict(cross_repo, "required_artifacts", errors)
    checks = _require_list(cross_repo, "ci_contract_checks", errors)
    api_guarantees = _require_dict(cross_repo, "api_compatibility_guarantees", errors)

    expected_line = ""
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        expected_line = f"{parts[0]}.{parts[1]}.x"
    else:
        errors.append(f"version must start with major.minor semver components: {version!r}")

    contract_line = str(cross_repo.get("contract_line", ""))
    policy_line = str(compatibility_policy.get("contract_semver", ""))
    if expected_line:
        if policy_line != expected_line:
            errors.append(f"compatibility_policy.contract_semver must be {expected_line}, got {policy_line}")
        if contract_line != expected_line:
            errors.append(f"cross_repo_contract.contract_line must be {expected_line}, got {contract_line}")

    if str(contract.get("contract_schema_version", "")) != "1.0.0":
        errors.append("contract_schema_version must be 1.0.0")

    repo = str(image.get("repository", ""))
    default_tag = str(image.get("default_tag", ""))
    expected_pin = f"{repo}:{default_tag}"
    if version != default_tag:
        errors.append(f"version ({version}) must match image.default_tag ({default_tag})")

    compose_matches = IMAGE_PIN_RE.findall(COMPOSE_PATH.read_text(encoding="utf-8"))
    if not compose_matches:
        errors.append("docker-compose.yml must include OPENBLADE_EMULATOR_IMAGE pin")
    elif set(compose_matches) != {expected_pin}:
        errors.append(f"docker-compose image pins must all equal {expected_pin}, got {sorted(set(compose_matches))}")

    if str(contract.get("scope_policy", "")) != str(matrix.get("scope", "")):
        errors.append("scope_policy must match matrix scope")

    matrix_minimum = int(matrix.get("minimum_cases_per_endpoint", 0))
    contract_minimum = int(contract.get("minimum_cases_per_endpoint", 0))
    if matrix_minimum < contract_minimum:
        errors.append(
            "matrix minimum_cases_per_endpoint must be >= contract minimum_cases_per_endpoint"
        )
    if int(api_guarantees.get("matrix_minimum_case_count", 0)) != contract_minimum:
        errors.append("api_compatibility_guarantees.matrix_minimum_case_count must match contract minimum")

    required_endpoints = api_guarantees.get("required_endpoints")
    if not isinstance(required_endpoints, list) or not {"/health", "/aml/"}.issubset(required_endpoints):
        errors.append("api_compatibility_guarantees.required_endpoints must include /health and /aml/")

    required_paths: list[str] = []
    for key in ("contract_file", "manual_matrix_file", "compose_file"):
        value = artifacts.get(key)
        if not isinstance(value, str):
            errors.append(f"required_artifacts.{key} must be a string path")
            continue
        required_paths.append(value)

    workflow_files = artifacts.get("workflow_files")
    if not isinstance(workflow_files, list) or not workflow_files:
        errors.append("required_artifacts.workflow_files must be a non-empty list")
        workflow_files = []
    else:
        required_paths.extend(path for path in workflow_files if isinstance(path, str))

    for relative_path in required_paths:
        if not (ROOT / relative_path).exists():
            errors.append(f"required artifact path does not exist: {relative_path}")

    for raw_check in checks:
        if not isinstance(raw_check, dict):
            errors.append("ci_contract_checks entries must be objects")
            continue
        check_id = raw_check.get("id")
        workflow_file = raw_check.get("workflow_file")
        command = raw_check.get("command")
        if not isinstance(check_id, str) or not check_id:
            errors.append("ci_contract_checks.id must be a non-empty string")
        if not isinstance(workflow_file, str) or not workflow_file:
            errors.append("ci_contract_checks.workflow_file must be a non-empty string")
            continue
        if not isinstance(command, str) or not command:
            errors.append("ci_contract_checks.command must be a non-empty string")
            continue
        workflow_path = ROOT / workflow_file
        if workflow_file not in workflow_files:
            errors.append(f"workflow file {workflow_file} must be listed in required_artifacts.workflow_files")
            continue
        if not workflow_path.exists():
            errors.append(f"workflow file does not exist: {workflow_file}")
            continue
        workflow_text = workflow_path.read_text(encoding="utf-8")
        if command not in workflow_text:
            errors.append(f"workflow {workflow_file} must run command: {command}")

    return errors


def main() -> int:
    errors = _validate()
    if errors:
        print("Cross-repository emulator contract validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Cross-repository emulator contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
