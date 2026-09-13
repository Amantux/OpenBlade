"""Deterministic rendering for every unit test.

Typer/rich render CLI output differently by terminal: color codes appear when
a CI environment force-enables them, and panels wrap at the detected width —
which split asserted phrases like `--path` across border characters. One test
failed ONLY on GitHub Actions because of exactly this. Pin the environment so
output is byte-stable everywhere: no color, wide virtual terminal.
"""

import pytest


@pytest.fixture(autouse=True)
def _deterministic_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")


_ANSI_RE = __import__("re").compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """For asserting on human-channel output: CI/terminals may force color."""
    return _ANSI_RE.sub("", text)
