"""Generated compliance suites derived from the manual endpoint matrix."""

from __future__ import annotations

from collections import Counter

import pytest

from tests.i3.manual_matrix_spec_suite import (
    MINIMUM_CASES_PER_RETURN_STATE_CLASS,
    REQUIRED_ENDPOINT_RETURN_STATE_CLASSES,
    build_generated_spec_cases,
    group_generated_cases_by_endpoint,
    load_manual_matrix,
)

pytestmark = pytest.mark.i3


def test_generated_suites_match_matrix_case_template_volume() -> None:
    matrix = load_manual_matrix()
    generated_cases = build_generated_spec_cases(matrix)
    endpoints = matrix["endpoints"]
    expected_case_total = sum(len(endpoint["case_templates"]) for endpoint in endpoints)
    assert len(generated_cases) == expected_case_total


def test_generated_suites_exactly_cover_manual_matrix_case_templates() -> None:
    matrix = load_manual_matrix()
    generated_cases = build_generated_spec_cases(matrix)

    expected_templates = {
        (endpoint["id"], template["id"])
        for endpoint in matrix["endpoints"]
        for template in endpoint["case_templates"]
    }
    generated_templates = {(case.endpoint_id, case.case_id) for case in generated_cases}
    assert generated_templates == expected_templates



def test_each_manual_endpoint_has_minimum_generated_cases() -> None:
    matrix = load_manual_matrix()
    generated_cases = build_generated_spec_cases(matrix)
    cases_by_endpoint = group_generated_cases_by_endpoint(generated_cases)

    matrix_minimum = int(matrix["minimum_cases_per_endpoint"])
    required_minimum = max(5, matrix_minimum)
    for endpoint in matrix["endpoints"]:
        endpoint_id = endpoint["id"]
        assert len(cases_by_endpoint[endpoint_id]) >= required_minimum



def test_each_endpoint_includes_known_bad_negative_and_state_transition_cases() -> None:
    generated_cases = build_generated_spec_cases()
    cases_by_endpoint = group_generated_cases_by_endpoint(generated_cases)

    for endpoint_id, endpoint_cases in cases_by_endpoint.items():
        assert any(case.is_known_bad_negative for case in endpoint_cases), endpoint_id
        assert any(case.is_state_transition for case in endpoint_cases), endpoint_id



def test_each_endpoint_spans_required_return_state_classes() -> None:
    generated_cases = build_generated_spec_cases()
    cases_by_endpoint = group_generated_cases_by_endpoint(generated_cases)

    for endpoint_id, endpoint_cases in cases_by_endpoint.items():
        endpoint_classes = {case.return_state_class for case in endpoint_cases}
        assert REQUIRED_ENDPOINT_RETURN_STATE_CLASSES.issubset(endpoint_classes), endpoint_id



def test_return_state_classes_have_policy_minimum_generated_depth() -> None:
    generated_cases = build_generated_spec_cases()
    class_counts = Counter(case.return_state_class for case in generated_cases)

    assert REQUIRED_ENDPOINT_RETURN_STATE_CLASSES.issubset(class_counts)
    for return_state_class in REQUIRED_ENDPOINT_RETURN_STATE_CLASSES:
        assert class_counts[return_state_class] >= MINIMUM_CASES_PER_RETURN_STATE_CLASS
