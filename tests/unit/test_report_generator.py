from __future__ import annotations

import json
from pathlib import Path

from openblade.testing.report_generator import ReportGenerator, TestResult, TestSuiteReport


def _sample_report() -> TestSuiteReport:
    return TestSuiteReport(
        generated_at="2024-01-01T00:00:00+00:00",
        total=3,
        passed=1,
        failed=1,
        error=0,
        skipped=1,
        duration_s=1.234,
        results=[
            TestResult("tests/unit/test_alpha.py::test_pass", "passed", 0.1),
            TestResult(
                "tests/unit/test_alpha.py::test_fail",
                "failed",
                0.2,
                error_message="AssertionError: boom",
            ),
            TestResult("tests/unit/test_alpha.py::test_skip", "skipped", 0.0),
        ],
    )


def test_to_json_is_valid_json() -> None:
    payload = json.loads(ReportGenerator().to_json(_sample_report()))

    assert payload["total"] == 3
    assert payload["results"][1]["outcome"] == "failed"


def test_to_markdown_includes_summary() -> None:
    markdown = ReportGenerator().to_markdown(_sample_report())

    assert "## Summary" in markdown
    assert "| Passed | 1 |" in markdown
    assert "| Total | 3 |" in markdown


def test_to_markdown_includes_failed_section_when_failures() -> None:
    markdown = ReportGenerator().to_markdown(_sample_report())

    assert "## Failed Tests" in markdown
    assert "AssertionError: boom" in markdown


def test_write_report_creates_both_files(tmp_path: Path) -> None:
    json_path, markdown_path = ReportGenerator().write_report(_sample_report(), tmp_path)

    assert json_path.exists()
    assert markdown_path.exists()
    assert json_path.read_text(encoding="utf-8")
    assert markdown_path.read_text(encoding="utf-8")


def test_suite_report_counts_consistent() -> None:
    report = _sample_report()

    assert report.total == len(report.results)
    assert report.total == report.passed + report.failed + report.error + report.skipped


def test_markdown_includes_generated_at_timestamp() -> None:
    markdown = ReportGenerator().to_markdown(_sample_report())

    assert "Generated at: 2024-01-01T00:00:00+00:00" in markdown
