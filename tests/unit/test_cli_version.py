"""`openblade --version`.

Version is single-sourced in pyproject.toml's [project].version; the CLI
reads it back via importlib.metadata rather than duplicating the string.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from openblade.cli import main as cli_main

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    expected = pyproject["project"]["version"]

    result = runner.invoke(cli_main.app, ["--version"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert result.output.strip() == expected


def test_version_is_eager_and_short_circuits_other_options() -> None:
    # --version must win even when paired with a nonsense subcommand: it is an
    # is_eager callback, so it must fire (and exit 0) before typer tries to
    # resolve "not-a-real-command" as a subcommand.
    result = runner.invoke(
        cli_main.app, ["--version", "not-a-real-command"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
