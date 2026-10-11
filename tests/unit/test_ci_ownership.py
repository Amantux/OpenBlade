"""Tests for tools/ci_ownership.py (changed-path -> CI category mapper)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "ci_ownership", ROOT / "tools" / "ci_ownership.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_ownership"] = module
    spec.loader.exec_module(module)
    return module


ci = _load()
RULES = ci.load_rules(ROOT / "tools" / "ci_ownership.toml")


def test_scheduler_change_selects_safety_and_unit() -> None:
    sel = ci.select(["openblade/jobs/scheduler.py"], RULES)
    assert {"unit", "safety"} <= sel.categories
    assert sel.unowned == ()


def test_docs_only_change_selects_only_docs() -> None:
    sel = ci.select(["docs/test-plan.md", "README.md"], RULES)
    assert sel.categories == frozenset({"docs-only"})
    assert sel.unowned == ()


def test_unowned_openblade_path_is_reported() -> None:
    sel = ci.select(["openblade/brand_new_pkg/mod.py", "tests/unit/test_x.py"], RULES)
    assert sel.unowned == ("openblade/brand_new_pkg/mod.py",)


def test_unowned_tests_path_is_reported() -> None:
    sel = ci.select(["tests/new_lane/test_y.py"], RULES)
    assert sel.unowned == ("tests/new_lane/test_y.py",)


def test_unmatched_path_outside_enforced_trees_is_not_a_failure() -> None:
    sel = ci.select(["scripts/whatever.sh"], RULES)
    assert sel.unowned == ()
    assert sel.categories == frozenset()


def test_main_exits_1_on_unowned_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert ci.main(["--paths", "openblade/brand_new_pkg/mod.py"]) == 1
    captured = capsys.readouterr()
    assert "UNOWNED" in captured.out
    assert "FAIL" in captured.err


def test_main_exits_0_and_prints_reasoning(capsys: pytest.CaptureFixture[str]) -> None:
    assert ci.main(["--paths", "openblade/domain/policies.py"]) == 0
    out = capsys.readouterr().out
    assert "openblade/domain/* -> unit,safety" in out
    assert "selected categories:" in out


def test_invalid_category_in_table_is_rejected(tmp_path: Path) -> None:
    table = tmp_path / "t.toml"
    table.write_text('[[rule]]\nglob = "x/*"\ncategories = ["bogus"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        ci.load_rules(table)


def test_every_tracked_file_is_owned() -> None:
    files = subprocess.run(
        ["git", "ls-files", "openblade", "tests"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert files
    assert ci.select(files, RULES).unowned == ()
