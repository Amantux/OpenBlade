"""Test report generator producing Markdown and JSON summaries of the OpenBlade test suite results."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TestResult:
    """Outcome data for a single pytest node."""

    __test__ = False

    node_id: str
    outcome: str
    duration_s: float
    error_message: str = ""
    markers: list[str] = field(default_factory=list)


@dataclass
class TestSuiteReport:
    """Summary of a pytest run."""

    __test__ = False

    generated_at: str
    total: int
    passed: int
    failed: int
    error: int
    skipped: int
    duration_s: float
    results: list[TestResult] = field(default_factory=list)
    suite_name: str = "OpenBlade"
    version: str = "0.2.0"


class ReportGenerator:
    """Generates Markdown and JSON test reports from pytest results."""

    def collect_results(self, test_dir: str = "tests/unit") -> TestSuiteReport:
        """Run pytest, collect results, and return a structured report."""
        collected_markers = self._collect_markers(test_dir)
        generated_at = datetime.now(timezone.utc).isoformat()
        report_path = Path(".pytest-json-report.json")
        command = [
            sys.executable,
            "-m",
            "pytest",
            test_dir,
            "-q",
            "--json-report",
            f"--json-report-file={report_path}",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if report_path.exists():
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            finally:
                report_path.unlink(missing_ok=True)
            return self._report_from_json_payload(payload, generated_at, collected_markers)
        if self._json_report_missing(completed.stderr):
            fallback = subprocess.run(
                [sys.executable, "-m", "pytest", test_dir, "-vv"],
                capture_output=True,
                text=True,
                check=False,
            )
            return self._report_from_stdout(fallback.stdout, generated_at, collected_markers)
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "pytest failed")

    def to_json(self, report: TestSuiteReport) -> str:
        """Serialize a test suite report to a JSON string."""
        return json.dumps(asdict(report), indent=2)

    def to_markdown(self, report: TestSuiteReport) -> str:
        """Generate a Markdown summary for a test suite report."""
        lines = [
            f"# {report.suite_name} Test Report",
            "",
            f"Generated at: {report.generated_at}",
            f"Version: {report.version}",
            "",
            "## Summary",
            "",
            "| Metric | Count |",
            "| --- | ---: |",
            f"| Passed | {report.passed} |",
            f"| Failed | {report.failed} |",
            f"| Error | {report.error} |",
            f"| Skipped | {report.skipped} |",
            f"| Total | {report.total} |",
            f"| Duration (s) | {report.duration_s:.3f} |",
            "",
        ]
        failed_results = [result for result in report.results if result.outcome in {"failed", "error"}]
        if failed_results:
            lines.extend(["## Failed Tests", ""])
            for result in failed_results:
                lines.append(f"### {result.node_id}")
                if result.error_message:
                    lines.extend(["```text", result.error_message, "```"])
                else:
                    lines.append("No error message captured.")
                lines.append("")
        lines.append(f"Skipped tests: {report.skipped}")
        return "\n".join(lines)

    def write_report(self, report: TestSuiteReport, output_dir: Path) -> tuple[Path, Path]:
        """Write JSON and Markdown report files to the given output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "openblade-test-report.json"
        markdown_path = output_dir / "openblade-test-report.md"
        json_path.write_text(self.to_json(report), encoding="utf-8")
        markdown_path.write_text(self.to_markdown(report), encoding="utf-8")
        return json_path, markdown_path

    def _collect_markers(self, test_dir: str) -> dict[str, list[str]]:
        command = [sys.executable, "-m", "pytest", test_dir, "--collect-only", "-q"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        markers: dict[str, list[str]] = {}
        if completed.returncode not in {0, 5}:
            return markers
        for line in completed.stdout.splitlines():
            node_id = line.strip()
            if not node_id or node_id.endswith("tests collected"):
                continue
            markers[node_id] = []
        return markers

    def _report_from_json_payload(
        self,
        payload: dict[str, Any],
        generated_at: str,
        collected_markers: dict[str, list[str]],
    ) -> TestSuiteReport:
        tests = payload.get("tests", [])
        summary = payload.get("summary", {})
        results = [
            TestResult(
                node_id=str(item.get("nodeid", "")),
                outcome=str(item.get("outcome", "error")),
                duration_s=float(item.get("duration", 0.0) or 0.0),
                error_message=self._extract_error_message(item),
                markers=collected_markers.get(str(item.get("nodeid", "")), []),
            )
            for item in tests
            if isinstance(item, dict)
        ]
        return TestSuiteReport(
            generated_at=generated_at,
            total=int(summary.get("total", len(results))),
            passed=int(summary.get("passed", 0)),
            failed=int(summary.get("failed", 0)),
            error=int(summary.get("error", 0)),
            skipped=int(summary.get("skipped", 0)),
            duration_s=float(payload.get("duration", 0.0) or 0.0),
            results=results,
        )

    def _report_from_stdout(
        self,
        stdout: str,
        generated_at: str,
        collected_markers: dict[str, list[str]],
    ) -> TestSuiteReport:
        results: list[TestResult] = []
        pattern = re.compile(
            r"^(?P<node>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)(?:\s+.*)?$"
        )
        for line in stdout.splitlines():
            match = pattern.match(line.strip())
            if match is None:
                continue
            node_id = match.group("node")
            outcome = match.group("outcome").lower()
            results.append(
                TestResult(
                    node_id=node_id,
                    outcome=outcome,
                    duration_s=0.0,
                    markers=collected_markers.get(node_id, []),
                )
            )
        duration_match = re.search(r"in\s+(\d+\.\d+)s", stdout)
        return TestSuiteReport(
            generated_at=generated_at,
            total=len(results),
            passed=sum(result.outcome == "passed" for result in results),
            failed=sum(result.outcome == "failed" for result in results),
            error=sum(result.outcome == "error" for result in results),
            skipped=sum(result.outcome == "skipped" for result in results),
            duration_s=float(duration_match.group(1)) if duration_match else 0.0,
            results=results,
        )

    @staticmethod
    def _extract_error_message(item: dict[str, Any]) -> str:
        for key in ("longrepr", "crash", "message"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                return json.dumps(value, indent=2)
        return ""

    @staticmethod
    def _json_report_missing(stderr: str) -> bool:
        return "--json-report" in stderr and "unrecognized arguments" in stderr
