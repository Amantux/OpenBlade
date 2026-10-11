"""Unit tests for the skip-as-pass gate (no rig needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.hardware import junit_gate


def _report(tmp_path: Path, body: str, name: str = "r.xml") -> Path:
    path = tmp_path / name
    path.write_text(f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>')
    return path


PASS = '<testcase classname="t.m" name="test_ok"/>'
SKIP = '<testcase classname="t.m" name="test_skip"><skipped message="no rig"/></testcase>'
FAIL = '<testcase classname="t.m" name="test_bad"><failure message="x"/></testcase>'


def test_all_passed_is_green(tmp_path: Path) -> None:
    assert junit_gate.main([str(_report(tmp_path, PASS))]) == 0


@pytest.mark.parametrize("body", [SKIP, PASS + SKIP, "", FAIL])
def test_skip_or_nothing_passed_is_red(tmp_path: Path, body: str) -> None:
    assert junit_gate.main([str(_report(tmp_path, body))]) == 1


def test_expected_skip_is_allowed(tmp_path: Path) -> None:
    path = _report(tmp_path, PASS + SKIP)
    assert junit_gate.main([str(path), "--expected-skip", "t.m::test_skip"]) == 0


def test_expected_skip_does_not_mask_zero_passed(tmp_path: Path) -> None:
    path = _report(tmp_path, SKIP)
    assert junit_gate.main([str(path), "--expected-skip", "t.m::test_skip"]) == 1


def test_missing_report_is_red(tmp_path: Path) -> None:
    assert junit_gate.main([str(tmp_path / "absent.xml")]) == 1


def test_multiple_reports_aggregate(tmp_path: Path) -> None:
    a = _report(tmp_path, PASS, "a.xml")
    b = _report(tmp_path, SKIP, "b.xml")
    assert junit_gate.main([str(a), str(b)]) == 1
