"""The wiki reference pages must match what the generator produces.

`docs/wiki/reference/cli.md` and `docs/wiki/reference/api.md` are build
artifacts of `tools/gen_wiki_reference.py`. Hand-maintained command and route
tables drift; this test makes the drift a red test instead of a wrong document.

If this fails, run::

    python3 tools/gen_wiki_reference.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "gen_wiki_reference.py"

REGENERATE_HINT = "Run: python3 tools/gen_wiki_reference.py"


def _load_generator() -> ModuleType:
    """Import the generator by path (``tools/`` is not an importable package)."""
    spec = importlib.util.spec_from_file_location("_gen_wiki_reference", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    return _load_generator()


def test_generator_script_exists() -> None:
    assert GENERATOR.is_file()


def test_reference_pages_are_not_stale(generator: ModuleType) -> None:
    for path, expected in generator.build_pages().items():
        relative = path.relative_to(ROOT)
        assert path.exists(), f"{relative} is missing. {REGENERATE_HINT}"
        actual = path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"{relative} is stale — the CLI or API surface changed. {REGENERATE_HINT}"
        )


def test_check_mode_reports_clean(generator: ModuleType) -> None:
    assert generator.check_pages() == []


def test_cli_page_covers_every_leaf_command(generator: ModuleType) -> None:
    """A guard against the generator silently emitting an empty tree.

    Typer vendors its own click fork, so an ``isinstance(cmd, click.Group)``
    check collapses the whole command tree into a single leaf without raising.
    """
    commands, groups = generator.collect_cli_commands()
    assert len(commands) > 5, "command tree looks collapsed"
    assert groups, "no command groups discovered"
    page = (ROOT / "docs" / "wiki" / "reference" / "cli.md").read_text(encoding="utf-8")
    for command in commands:
        invocation = " ".join(("openblade", *command.path))
        assert f"### `{invocation}`" in page


def test_api_page_separates_native_from_emulator_surface(generator: ModuleType) -> None:
    grouped = generator.collect_operations()
    native = [tag for tag in grouped if not generator._is_emulator_tag(tag)]
    emulator = [tag for tag in grouped if generator._is_emulator_tag(tag)]
    assert native and emulator
    page = (ROOT / "docs" / "wiki" / "reference" / "api.md").read_text(encoding="utf-8")
    # Emulator paths must NOT be expanded inline: that surface has its own
    # generated catalog and ~1000 rows here would bury the control plane.
    assert "/aml/system/status" not in page
    for tag in ("jobs", "archive", "restore", "inventory"):
        assert f"### `{tag}`" in page
