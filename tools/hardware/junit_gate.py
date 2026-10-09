"""Fail a rig lane that "passed" by skipping.

Rig tests skip when their prerequisites are missing, and pytest exits 0 on an
all-skipped run. This gate reads the JUnit XML each rig pytest step wrote and
exits 1 if any test was skipped (outside an explicit expected-skip list) or if
nothing passed at all.

    python -m tools.hardware.junit_gate reports/*.xml [--expected-skip NODEID ...]
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET  # parses our own pytest output, not untrusted input
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GateResult:
    passed: int = 0
    failed: int = 0
    skipped: list[str] = field(default_factory=list)


def _node_id(case: ET.Element) -> str:
    classname = case.get("classname", "")
    name = case.get("name", "")
    return f"{classname}::{name}" if classname else name


def collect(paths: list[Path]) -> GateResult:
    result = GateResult()
    for path in paths:
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                result.skipped.append(_node_id(case))
            elif case.find("failure") is not None or case.find("error") is not None:
                result.failed += 1
            else:
                result.passed += 1
    return result


def evaluate(result: GateResult, expected_skips: set[str]) -> list[str]:
    """Return the reasons the gate fails (empty list = pass)."""
    problems: list[str] = []
    unexpected = sorted(s for s in result.skipped if s not in expected_skips)
    if unexpected:
        problems.append(f"{len(unexpected)} unexpected skip(s): " + ", ".join(unexpected))
    if result.passed == 0:
        problems.append("0 tests passed")
    if result.failed:
        problems.append(f"{result.failed} test(s) failed or errored")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument(
        "--expected-skip",
        action="append",
        default=[],
        help="JUnit classname::name of a skip that is expected (repeatable)",
    )
    args = parser.parse_args(argv)
    missing = [str(p) for p in args.reports if not p.is_file()]
    if missing:
        print("::error::missing JUnit report(s): " + ", ".join(missing), file=sys.stderr)
        return 1
    result = collect(args.reports)
    problems = evaluate(result, set(args.expected_skip))
    print(f"passed={result.passed} failed={result.failed} skipped={len(result.skipped)}")
    for problem in problems:
        print(f"::error::rig lane gate: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
