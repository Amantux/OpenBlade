"""The assistant's safety line: it reads, explains, and proposes. It never acts.

These are the regression tests for that guarantee.

The two structural guards -- the registry allowlist and the ReadOnlyProxy attribute
allowlist -- are mutation-checked: disabling either makes the tests in sections 1
and 2 fail (verified; see the docstrings, and
``test_registry_guard_rejects_a_mutating_tool``, which performs a mutation inline).

Section 4 is different and is labelled as such: the prompt tests assert the text
of an instruction, and there is no guard to remove. They catch a prompt edit that
drops the safety contract, nothing more -- the prompt is guidance, and
:mod:`openblade.assistant.readonly` is what actually makes execution impossible.
"""

from __future__ import annotations

import ast
import copy
import pickle
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
    """Naming hygiene only -- ``get_and_format_tape`` would pass this.

    The registry allowlist above is the guard; this just keeps the exposed surface
    legible at a glance.
    """
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
        # Repository writes.
        "create_volume_group",
        "add_cartridge",
        "create_file_record",
        "delete_file_record",
        "save_safety_token",
        "update_job_state",
        # Routes to the live session / ORM.
        "session",
        "_session",
        # Escapes an earlier __getattr__ + __slots__ design actually leaked:
        # slot descriptors are found by NORMAL lookup, so __getattr__ never ran
        # and proxy._target handed back the live CatalogRepository.
        "_target",
        "_allowed",
        "_label",
        "__class__",
        "__dict__",
        "__init__",
        "__reduce__",
        "__reduce_ex__",
        "__getstate__",
        "__getattribute__",
    ],
)
def test_read_only_catalog_blocks_writes(app_context: Any, method: str) -> None:
    """Mutation: widen CATALOG_READ_METHODS or drop the __getattribute__ check -> fails."""
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


def test_read_only_proxy_cannot_be_re_initialised(app_context: Any) -> None:
    """__setattr__ is not enough on its own.

    ``__init__`` uses ``object.__setattr__`` internally, so while ``__setattr__``
    was blocked, calling ``proxy.__init__(evil, {"create_volume_group"}, "catalog")``
    re-pointed the proxy at an arbitrary object with an arbitrary allowlist. Every
    attribute access, ``__init__`` included, must go through the guard.
    """
    proxy = read_only_catalog(app_context.catalog)
    with pytest.raises(ReadOnlyViolationError):
        proxy.__init__(app_context.catalog, frozenset({"create_volume_group"}), "catalog")
    # Still guarded afterwards.
    with pytest.raises(ReadOnlyViolationError):
        getattr(proxy, "create_volume_group")  # noqa: B009 - the lookup IS the test


def test_read_only_proxy_cannot_be_copied_or_pickled(app_context: Any) -> None:
    """copy/pickle reach for __reduce_ex__ on the instance; that must be refused,
    not answered with a reconstructable view of the live object."""
    proxy = read_only_catalog(app_context.catalog)
    with pytest.raises(ReadOnlyViolationError):
        copy.copy(proxy)
    with pytest.raises(ReadOnlyViolationError):
        pickle.dumps(proxy)


# ---------------------------------------------------------------------------
# 3. The module itself contains no execution or write path
# ---------------------------------------------------------------------------


# Every Python file in the package, spelled out. A new file is a deliberate diff,
# and the source scans below cannot silently stop covering part of the package.
ASSISTANT_SOURCE_NAMES = frozenset(
    {
        "__init__.py",
        "config.py",
        "errors.py",
        "prompts.py",
        "provider.py",
        "readonly.py",
        "session.py",
        "tools.py",
    }
)


def _assistant_sources() -> list[Path]:
    """rglob, not glob.

    With ``glob("*.py")`` the scans below covered only the top level, so a
    subpackage containing ``import subprocess`` and ``session.commit()`` passed the
    entire safety suite. Verified: adding such a module left 41 tests green.
    """
    return sorted(ASSISTANT_DIR.rglob("*.py"))


def test_assistant_sources_are_the_reviewed_set() -> None:
    """Guards the source-scanning tests below from silently under-covering.

    A new module — at the top level or in a subpackage — fails here until it is
    named, which is the prompt to review it for writes and execution paths.
    """
    found = {path.relative_to(ASSISTANT_DIR).as_posix() for path in _assistant_sources()}
    assert found == set(ASSISTANT_SOURCE_NAMES)


@pytest.mark.parametrize(
    "forbidden",
    [
        # stdlib execution surfaces
        "subprocess",
        "pty",
        "shutil",
        "ctypes",
        "multiprocessing",
        # OpenBlade's own acting subsystems. hardware.runner.SafeRunner is exactly
        # the thing that shells out, and jobs/ performs archives, restores and
        # formats -- importing any of them would route round the tool registry.
        "openblade.hardware",
        "openblade.jobs",
        "openblade.safety",
        "openblade.fuse",
        "openblade.sftp",
        "openblade.simulator",
    ],
)
def test_assistant_never_imports_an_execution_module(forbidden: str) -> None:
    """No tool may shell out, and none may reach an OpenBlade module that acts.

    The repo rule is "never shell=True"; here it is "never shell, and never import
    something that does"."""

    def _blocked(module: str) -> bool:
        return module == forbidden or module.startswith(forbidden + ".")

    for path in _assistant_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(_blocked(alias.name) for alias in node.names), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not _blocked(node.module), f"{path}:{node.lineno}"


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
    assert "You are a read-only advisor. You cannot run anything." in SYSTEM_PROMPT
    assert (
        "there is no tool that loads, unloads, moves, formats, erases, archives, restores,\n"
        "or writes anything at all, and no tool that shells out."
    ) in SYSTEM_PROMPT
    assert (
        "When the operator needs something done, you PROPOSE the exact command and they run"
    ) in SYSTEM_PROMPT
    assert "Review before running. OpenBlade treats tape automation as destructive." in (
        SYSTEM_PROMPT
    )


def test_prompt_bakes_in_the_two_phase_destructive_flow() -> None:
    """Mirrors docs/safety.md; if the flow changes, this must change with it."""
    assert "openblade format dry-run --barcode" in SYSTEM_PROMPT
    assert "openblade format confirm --barcode" in SYSTEM_PROMPT
    assert "one-time safety token" in SYSTEM_PROMPT
    assert "OPENBLADE_REAL_HARDWARE_ENABLED=true" in SYSTEM_PROMPT
    assert "never present step 3 alone" in SYSTEM_PROMPT.lower()


def test_prompt_instructs_refusal_of_gate_bypass() -> None:
    """Whole sentences, because a keyword check cannot tell an instruction from its
    negation -- `"for testing only" in prompt` passes whether the prompt forbids a
    bypass or offers one."""
    assert (
        "If asked how to skip, disable, forge, patch out or otherwise bypass a safety gate"
    ) in SYSTEM_PROMPT
    assert "refuse. Say plainly that you will not" in SYSTEM_PROMPT
    assert (
        'Do not provide a partial bypass, a "for testing only" variant, or the name\n'
        "of the source file to edit."
    ) in SYSTEM_PROMPT
