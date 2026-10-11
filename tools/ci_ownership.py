"""Map changed paths to the CI test categories that own them.

Usage (CI calls it this way, before any dependency is installed, so this
module is stdlib-only)::

    python tools/ci_ownership.py --base origin/master
    python tools/ci_ownership.py --paths openblade/jobs/scheduler.py docs/x.md

Prints the per-path selection reasoning and the selected category set. Exits
1 when any changed ``openblade/**`` or ``tests/**`` path has no owning
category in ``tools/ci_ownership.toml``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

CATEGORIES = frozenset(
    {"unit", "integration", "safety", "i3", "compat", "frontend", "docs-only", "ci-only"}
)
ENFORCED_PREFIXES = ("openblade/", "tests/")
DEFAULT_TABLE = Path(__file__).with_name("ci_ownership.toml")


@dataclass(frozen=True)
class Rule:
    glob: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    owners: dict[str, tuple[tuple[str, tuple[str, ...]], ...]]
    """path -> ((matching glob, its categories), ...)"""
    categories: frozenset[str]
    unowned: tuple[str, ...]
    """Enforced (openblade/ or tests/) paths that no rule matched."""


def load_rules(table: Path) -> list[Rule]:
    data = tomllib.loads(table.read_text(encoding="utf-8"))
    rules: list[Rule] = []
    for raw in data.get("rule", []):
        cats = tuple(raw["categories"])
        bad = set(cats) - CATEGORIES
        if bad or not cats:
            raise ValueError(f"rule {raw['glob']!r}: invalid categories {sorted(bad) or '[]'}")
        rules.append(Rule(glob=raw["glob"], categories=cats))
    return rules


def select(paths: Sequence[str], rules: Sequence[Rule]) -> Selection:
    """Pure mapping: changed paths -> owning categories + unowned enforced paths."""
    owners: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {}
    categories: set[str] = set()
    unowned: list[str] = []
    for path in paths:
        matches = tuple((r.glob, r.categories) for r in rules if fnmatchcase(path, r.glob))
        owners[path] = matches
        for _, cats in matches:
            categories.update(cats)
        if not matches and path.startswith(ENFORCED_PREFIXES):
            unowned.append(path)
    return Selection(owners=owners, categories=frozenset(categories), unowned=tuple(unowned))


def changed_paths(base: str) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def report(selection: Selection) -> str:
    lines: list[str] = []
    for path, matches in selection.owners.items():
        if matches:
            why = "; ".join(f"{glob} -> {','.join(cats)}" for glob, cats in matches)
            lines.append(f"  {path}: {why}")
        elif path in selection.unowned:
            lines.append(f"  {path}: UNOWNED (no rule in ci_ownership.toml)")
        else:
            lines.append(f"  {path}: no rule (outside openblade/ and tests/, not enforced)")
    lines.append(f"selected categories: {', '.join(sorted(selection.categories)) or '(none)'}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base", help="git ref to diff against (base...HEAD)")
    source.add_argument("--paths", nargs="+", help="explicit paths instead of a git diff")
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    args = parser.parse_args(argv)

    paths = list(args.paths) if args.paths else changed_paths(args.base)
    selection = select(paths, load_rules(args.table))
    print(f"ci-ownership: {len(paths)} changed path(s)")
    print(report(selection))
    if selection.unowned:
        print(
            f"ci-ownership: FAIL - {len(selection.unowned)} path(s) under openblade/ or tests/ "
            "have no owning category; add a rule to tools/ci_ownership.toml",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
