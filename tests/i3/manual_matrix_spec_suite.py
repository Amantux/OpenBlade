"""Spec-derived test-case generation from the manual endpoint matrix."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = REPO_ROOT / "openblade" / "emulator_contract" / "quantum_i3_rev_h_matrix.json"

CASE_KIND_TO_RETURN_STATE_CLASS: dict[str, str] = {
    "happy-path": "success",
    "response-contract": "success",
    "state-transition": "state-transition",
    "known-bad-auth": "negative-auth",
    "known-bad-params": "negative-validation",
    "known-bad-payload": "negative-validation",
}

REQUIRED_ENDPOINT_RETURN_STATE_CLASSES = {
    "success",
    "state-transition",
    "negative-auth",
    "negative-validation",
}
MINIMUM_CASES_PER_RETURN_STATE_CLASS = 5


@dataclass(frozen=True)
class GeneratedSpecCase:
    endpoint_id: str
    method: str
    path: str
    case_id: str
    kind: str
    return_state_class: str

    @property
    def is_known_bad_negative(self) -> bool:
        return self.kind.startswith("known-bad")

    @property
    def is_state_transition(self) -> bool:
        return self.kind == "state-transition"


def load_manual_matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def build_generated_spec_cases(matrix: dict[str, object] | None = None) -> list[GeneratedSpecCase]:
    loaded_matrix = matrix if matrix is not None else load_manual_matrix()
    endpoints = loaded_matrix.get("endpoints")
    if not isinstance(endpoints, list):
        raise ValueError("matrix endpoints must be an array")

    generated_cases: list[GeneratedSpecCase] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("matrix endpoint entries must be objects")
        endpoint_id = str(endpoint.get("id", ""))
        method = str(endpoint.get("method", ""))
        path = str(endpoint.get("path", ""))
        case_templates = endpoint.get("case_templates")
        if not isinstance(case_templates, list):
            raise ValueError(f"endpoint {endpoint_id} case_templates must be an array")

        for template in case_templates:
            if not isinstance(template, dict):
                raise ValueError(f"endpoint {endpoint_id} case_templates entries must be objects")
            template_id = str(template.get("id", ""))
            kind = str(template.get("kind", ""))
            return_state_class = CASE_KIND_TO_RETURN_STATE_CLASS.get(kind)
            if return_state_class is None:
                raise ValueError(
                    f"endpoint {endpoint_id} case template {template_id} has unsupported kind {kind!r}"
                )
            generated_cases.append(
                GeneratedSpecCase(
                    endpoint_id=endpoint_id,
                    method=method,
                    path=path,
                    case_id=template_id,
                    kind=kind,
                    return_state_class=return_state_class,
                )
            )
    return generated_cases


def group_generated_cases_by_endpoint(cases: list[GeneratedSpecCase]) -> dict[str, list[GeneratedSpecCase]]:
    grouped: dict[str, list[GeneratedSpecCase]] = defaultdict(list)
    for case in cases:
        grouped[case.endpoint_id].append(case)
    return dict(grouped)
