"""The assistant's safety line: it reads, explains, and proposes. It never acts.

These are the regression tests for that guarantee. Each one is mutation-checked:
removing the guard it covers makes it fail (see the docstrings, which name the
mutation, and ``test_registry_guard_rejects_a_mutating_tool``, which performs one
inside the test).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from openblade.assistant.errors import ReadOnlyViolationError, ToolRegistryViolationError
from openblade.assistant.prompts import SYSTEM_PROMPT
from openblade.assistant.readonly import (
    CATALOG_READ_METHODS,
    LIBRARY_READ_METHODS,
    read_only_catalog,
    read_only_library,
)
from openblade.assistant.tools import (
    READ_ONLY_TOOL_NAMES,
    ReadOnlyTool,
    build_registry,
)

ASSISTANT_DIR = Path(__file__).resolve().parents[2] / "openblade" / "assistant"

# Verb prefixes that mark a repository method as mutating. Used to prove the
# read-allowlists contain nothing that writes.
_MUTATING_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "upsert_",
    "save_",
    "add_",
    "set_",
    "mark_",
    "revoke_",
    "deactivate_",
    "seed_",
    "bulk_",
    "init_",
    "run_",
    "load",
    "unload",
    "move",
    "format",
    "erase",
    "write",
    "archive",
    "restore",
)


# ---------------------------------------------------------------------------
# 1. The registry exposes exactly the allowlist, and nothing that mutates
# ---------------------------------------------------------------------------


def test_registry_matches_the_allowlist_exactly() -> None:
    """Mutation: add a name to READ_ONLY_TOOL_NAMES without a tool -> fails."""
    assert build_registry().names == READ_ONLY_TOOL_NAMES


def test_allowlist_is_the_reviewed_set() -> None:
    """The literal set, spelled out, so widening it shows up in a diff."""
    reviewed = {
        "get_inventory",
        "list_volume_groups",
        "get_volume_group",
        "list_jobs",
        "get_job",
        "catalog_search",
        "get_config_summary",
        "search_docs",
    }
    assert set(READ_ONLY_TOOL_NAMES) == reviewed


def test_no_tool_name_reads_as_mutating() -> None:
    """Every exposed tool is a get/list/search. Nothing acts."""
    for name in build_registry().names:
        assert name.startswith(("get_", "list_", "search_", "catalog_search")), name
        assert not name.startswith(_MUTATING_PREFIXES), name


def test_registry_guard_rejects_a_mutating_tool() -> None:
    """THE MUTATION CHECK.

    Register a tool that would move media. The registry must refuse to build. If
    the allowlist check in ``ToolRegistry.__init__`` is removed, this test fails —
    which is the point: a future tool addition fails closed.
    """

    def _load_tape(context: Any, arguments: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("a mutating tool must never be reachable")

    rogue = ReadOnlyTool(
        name="load_tape",
        description="Load a tape into a drive.",
        parameters={"type": "object", "properties": {}},
        handler=_load_tape,
    )
    with pytest.raises(ToolRegistryViolationError) as excinfo:
        build_registry([rogue])
    assert "load_tape" in str(excinfo.value)
    assert "read-only allowlist" in str(excinfo.value)

    # And the guard is not name-specific: any unlisted tool is refused.
    benign_but_unlisted = ReadOnlyTool(
        name="get_weather",
        description="Unrelated.",
        parameters={"type": "object", "properties": {}},
        handler=_load_tape,
    )
    with pytest.raises(ToolRegistryViolationError):
        build_registry([benign_but_unlisted])


def test_registry_rejects_a_duplicate_name() -> None:
    """A shadowing re-registration must not silently replace a vetted tool."""
    original = build_registry().get("get_inventory")
    with pytest.raises(ToolRegistryViolationError, match="Duplicate"):
        build_registry([original])


# ---------------------------------------------------------------------------
# 2. The read-only proxies: tools cannot reach a write method at all
# ---------------------------------------------------------------------------


def test_catalog_read_allowlist_contains_nothing_mutating() -> None:
    for name in CATALOG_READ_METHODS:
        assert not name.startswith(_MUTATING_PREFIXES), name


def test_library_read_allowlist_is_only_inventory() -> None:
    """load/unload/move are the media-moving calls on LibraryBackend."""
    assert set(LIBRARY_READ_METHODS) == {"inventory"}


@pytest.mark.parametrize(
    "method",
    [
        "create_volume_group",
        "add_cartridge",
        "create_file_record",
        "delete_file_record",
        "save_safety_token",
        "update_job_state",
        "session",
        "_session",
    ],
)
def test_read_only_catalog_blocks_writes(app_context: Any, method: str) -> None:
    """Mutation: widen CATALOG_READ_METHODS or drop the __getattr__ check -> fails."""
    proxy = read_only_catalog(app_context.catalog)
    with pytest.raises(ReadOnlyViolationError):
        getattr(proxy, method)


@pytest.mark.parametrize("method", ["load", "unload", "move"])
def test_read_only_library_blocks_media_moves(app_context: Any, method: str) -> None:
    proxy = read_only_library(app_context.library)
    with pytest.raises(ReadOnlyViolationError):
        getattr(proxy, method)


def test_read_only_proxy_still_serves_reads(app_context: Any) -> None:
    """The guard must not be vacuous: permitted reads do work."""
    assert read_only_library(app_context.library).inventory() is not None
    assert read_only_catalog(app_context.catalog).list_volume_groups() is not None


def test_read_only_proxy_cannot_be_rebound(app_context: Any) -> None:
    """No escape hatch: you cannot swap the target or smuggle in an attribute."""
    proxy = read_only_catalog(app_context.catalog)
    with pytest.raises(ReadOnlyViolationError):
        proxy.create_volume_group = lambda name: None  # type: ignore[misc]
    with pytest.raises(ReadOnlyViolationError):
        del proxy.list_volume_groups  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. The module itself contains no execution or write path
# ---------------------------------------------------------------------------


def _assistant_sources() -> list[Path]:
    return sorted(ASSISTANT_DIR.glob("*.py"))


def test_assistant_package_has_sources() -> None:
    """Guards the two source-scanning tests below from passing vacuously."""
    assert len(_assistant_sources()) >= 6


@pytest.mark.parametrize("forbidden", ["subprocess", "pty", "shutil", "ctypes", "multiprocessing"])
def test_assistant_never_imports_an_execution_module(forbidden: str) -> None:
    """No tool may shell out. The repo's rule is: never shell=True, and here,
    never shell at all."""
    for path in _assistant_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] != forbidden for alias in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != forbidden, path


@pytest.mark.parametrize(
    "forbidden", ["system", "popen", "execv", "execve", "spawnv", "fork", "eval", "exec"]
)
def test_assistant_never_calls_an_execution_builtin(forbidden: str) -> None:
    """``os`` is imported for ``os.environ``; these are the calls that matter."""
    for path in _assistant_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr != forbidden, f"{path}:{node.lineno}"
            elif isinstance(node.func, ast.Name):
                assert node.func.id != forbidden, f"{path}:{node.lineno}"


@pytest.mark.parametrize("forbidden", ["commit", "flush", "rollback", "execute"])
def test_assistant_never_calls_a_session_write(forbidden: str) -> None:
    """No `session.commit()` anywhere in the assistant: reads only, and the
    caller owns the transaction."""
    for path in _assistant_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != forbidden, f"{path}:{node.lineno}"


# ---------------------------------------------------------------------------
# 4. The prompt carries the proposal convention and the refusal instruction
# ---------------------------------------------------------------------------


def test_prompt_states_the_read_only_boundary() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "you cannot run anything" in lowered
    assert "propose" in lowered
    assert "review before running" in lowered


def test_prompt_bakes_in_the_two_phase_destructive_flow() -> None:
    """Mirrors docs/safety.md; if the flow changes, this must change with it."""
    assert "openblade format dry-run --barcode" in SYSTEM_PROMPT
    assert "openblade format confirm --barcode" in SYSTEM_PROMPT
    assert "one-time safety token" in SYSTEM_PROMPT
    assert "OPENBLADE_REAL_HARDWARE_ENABLED=true" in SYSTEM_PROMPT
    assert "never present step 3 alone" in SYSTEM_PROMPT.lower()


def test_prompt_instructs_refusal_of_gate_bypass() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "bypass a safety gate" in lowered
    assert "refuse" in lowered
    assert "for testing only" in lowered
