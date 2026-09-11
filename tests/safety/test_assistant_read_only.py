"""The assistant's safety line: it reads, explains, and proposes.

The one thing it may *do* is a tier-1 setup action the operator confirmed in the
REPL — create a volume group, add existing tapes to one. These are the regression
tests for both halves of that guarantee: everything else is unreachable, and the
executable part is unreachable without a yes.

The structural guards are mutation-checked, meaning removing the guard makes a
named test fail (verified, not assumed):

* the read-only registry allowlist and the ReadOnlyProxy attribute allowlist
  (sections 1-2; ``test_registry_guard_rejects_a_mutating_tool`` mutates inline);
* the write-path AST scan (section 5, ``test_write_path_scan_catches_a_rogue_module``);
* the setup allowlist AND the destructive-verb denylist (section 6 — the denylist
  case widens the allowlist on purpose, so only the denylist can be what fails it);
* the facade's attribute allowlist (section 7).

Sections 4 and 8 are different and are labelled as such: the prompt tests assert
the text of an instruction, and there is no guard to remove. They catch a prompt
edit that drops the safety contract, nothing more -- the prompt is guidance, and
:mod:`openblade.assistant.readonly` is what actually makes execution impossible.
"""

from __future__ import annotations

import ast
import copy
import pickle
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from openblade.assistant.errors import (
    ReadOnlyViolationError,
    SetupFacadeViolationError,
    SetupRegistryViolationError,
    ToolRegistryViolationError,
)
from openblade.assistant.prompts import SETUP_SYSTEM_PROMPT, SYSTEM_PROMPT
from openblade.assistant.readonly import (
    CATALOG_READ_METHODS,
    INVENTORY_READ_METHODS,
    AllowlistProxy,
    read_only_catalog,
    read_only_inventory,
)
from openblade.assistant.setup_facade import SetupFacade, setup_facade
from openblade.assistant.setup_tools import (
    SETUP_TOOL_NAMES,
    SetupTool,
    build_setup_registry,
    reject_destructive_name,
)
from openblade.assistant.tools import (
    READ_ONLY_TOOL_NAMES,
    ReadOnlyTool,
    build_context,
    build_registry,
)
from tests.assistant_support import assistant_config

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


def test_inventory_read_allowlist_is_only_snapshot() -> None:
    """The assistant is handed InventoryService, never the LibraryBackend.

    So load/unload/move/eject are not merely un-allowlisted -- they are not
    attributes of the wrapped object at all. This also keeps SAFETY_003
    (openblade/safety/import_guard.py) satisfied without widening its allowlist:
    the service layer already existed and the assistant now goes through it.
    """
    assert set(INVENTORY_READ_METHODS) == {"snapshot"}


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


@pytest.mark.parametrize("method", ["load", "unload", "move", "eject", "inventory", "library"])
def test_read_only_inventory_blocks_media_moves(app_context: Any, method: str) -> None:
    """Belt and braces: even if a LibraryBackend were passed in by mistake, the
    media-moving calls are refused by the allowlist."""
    proxy = read_only_inventory(app_context.inventory_service)
    with pytest.raises(ReadOnlyViolationError):
        getattr(proxy, method)


def test_assistant_context_never_holds_the_library_backend(app_context: Any) -> None:
    """The tool context must expose the service, not the hardware abstraction."""
    from openblade.assistant.tools import build_context

    context = build_context(
        config=assistant_config(),
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url="sqlite:///x.db",
    )
    assert not hasattr(context, "library")
    assert context.inventory.snapshot() is not None


def test_read_only_proxy_still_serves_reads(app_context: Any) -> None:
    """The guard must not be vacuous: permitted reads do work."""
    assert read_only_inventory(app_context.inventory_service).snapshot() is not None
    assert read_only_catalog(app_context.catalog).list_volume_groups() is not None


@pytest.mark.parametrize("attribute", ["__self__", "__func__", "__closure__", "__class__"])
def test_a_read_proxy_callable_does_not_hand_back_the_repository(
    app_context: Any, attribute: str
) -> None:
    """The escape found by attacking this module, not by a failing test.

    ``proxy.list_volume_groups`` used to be a *bound method*, and a bound method
    advertises its receiver: ``proxy.list_volume_groups.__self__.create_volume_group``
    was a live write, straight through the allowlist. Callables returned by a proxy
    are now sealed. Mutation: drop the SealedCall wrap in ``AllowlistProxy`` -> fails.
    """
    reader = read_only_catalog(app_context.catalog).list_volume_groups
    assert reader() is not None  # still callable
    with pytest.raises(ReadOnlyViolationError):
        getattr(reader, attribute)


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
        "setup_facade.py",
        "setup_tools.py",
        "tools.py",
    }
)

# The repository methods that write. Only ``setup_facade.py`` may name one, so a
# write cannot appear anywhere else in the package -- not in a tool body, not in
# the loop, not by typo.
CATALOG_WRITE_METHODS = frozenset(
    {
        "create_volume_group",
        "add_cartridge",
        "add_barcode_to_volume_group",
        "create_file_record",
        "create_file_instance",
        "delete_file_record",
        "save_safety_token",
        "create_job",
        "update_job_state",
        "mark_instance_archived",
    }
)

WRITE_PATH_OWNER = "setup_facade.py"


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


# ---------------------------------------------------------------------------
# 5. The write path: only the facade may touch it
# ---------------------------------------------------------------------------


def _write_path_offenders(paths: Iterable[Path]) -> list[str]:
    """Files naming a catalog write method, as ``name:line`` strings."""
    offenders: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in CATALOG_WRITE_METHODS:
                offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def test_only_the_facade_names_a_catalog_write_method() -> None:
    """The write path lives in exactly one file, and the scan proves it.

    Tier-1 tools call ``facade.new_volume_group``/``facade.attach_tapes``; the
    repository methods those wrap are named nowhere else in the package, so a write
    cannot appear in a tool body, in the loop, or via a copy-paste.
    """
    others = [
        path for path in _assistant_sources() if path.name != WRITE_PATH_OWNER
    ]
    assert _write_path_offenders(others) == []
    # And the guard is not vacuous: the facade itself does name them.
    facade = [path for path in _assistant_sources() if path.name == WRITE_PATH_OWNER]
    assert _write_path_offenders(facade), "the facade should be the one file that writes"


def test_write_path_scan_catches_a_rogue_module(tmp_path: Path) -> None:
    """THE MUTATION CHECK for the scan above.

    The rogue-subpackage pattern: a module that reaches the repository directly must
    be flagged. Written to a temp tree rather than into the package, so the check
    itself cannot leave a landmine behind.
    """
    rogue = tmp_path / "rogue.py"
    rogue.write_text(
        "def sneak(catalog):\n    return catalog.create_volume_group('mine')\n",
        encoding="utf-8",
    )
    assert _write_path_offenders([rogue]) == ["rogue.py:2"]


# ---------------------------------------------------------------------------
# 6. Tier 1: the setup registry allowlist and the destructive-verb denylist
# ---------------------------------------------------------------------------


def test_setup_allowlist_is_the_reviewed_set() -> None:
    """Two actions, spelled out, so widening tier 1 shows up in a diff."""
    assert set(SETUP_TOOL_NAMES) == {"create_volume_group", "add_tapes_to_volume_group"}
    assert build_setup_registry().names == SETUP_TOOL_NAMES


def test_setup_registry_rejects_an_unlisted_tool() -> None:
    """MUTATION CHECK: drop the allowlist check in ``SetupToolRegistry`` -> fails."""
    rogue = SetupTool(
        name="assign_everything",
        description="Unlisted.",
        parameters={"type": "object", "properties": {}},
        normalize=lambda arguments: {},
        plan=lambda facade, arguments: {},
        describe=lambda arguments, plan: "nope",
        apply=lambda facade, arguments: {},
    )
    with pytest.raises(SetupRegistryViolationError) as excinfo:
        build_setup_registry([rogue])
    assert "setup allowlist" in str(excinfo.value)


@pytest.mark.parametrize(
    "name",
    [
        "format_tape",
        "load_cartridge",
        "unload_drive",
        "move_tape",
        "eject_magazine",
        "delete_volume_group",
        "erase_pool",
        "wipe_catalog",
        "restore_files",
        "archive_directory",
        "write_tape",
    ],
)
def test_denylist_rejects_a_destructive_name_even_if_allowlisted(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE MUTATION CHECK for the denylist, and it is belt-and-braces on purpose.

    The allowlist is *widened for this test* to include the destructive name -- which
    is exactly the mistake the denylist exists to catch. Remove the
    ``reject_destructive_name`` call from ``SetupToolRegistry.__init__`` and these
    cases pass, which is how we know the denylist and not the allowlist is doing the
    work here.
    """
    monkeypatch.setattr(
        "openblade.assistant.setup_tools.SETUP_TOOL_NAMES",
        frozenset({*SETUP_TOOL_NAMES, name}),
    )
    rogue = SetupTool(
        name=name,
        description="Should never be registrable.",
        parameters={"type": "object", "properties": {}},
        normalize=lambda arguments: {},
        plan=lambda facade, arguments: {},
        describe=lambda arguments, plan: "nope",
        apply=lambda facade, arguments: {},
    )
    with pytest.raises(SetupRegistryViolationError) as excinfo:
        build_setup_registry([rogue])
    assert "destructive verb" in str(excinfo.value)


def test_denylist_also_polices_the_allowlist_itself() -> None:
    """Adding a destructive name to SETUP_TOOL_NAMES fails on the edit, at import."""
    for name in SETUP_TOOL_NAMES:
        reject_destructive_name(name)
    with pytest.raises(SetupRegistryViolationError):
        reject_destructive_name("delete_volume_group")


def test_no_setup_tool_is_also_a_read_tool() -> None:
    """The two registries must not share a name, or dispatch becomes ambiguous."""
    assert not (SETUP_TOOL_NAMES & READ_ONLY_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 7. The write facade, attacked the way ReadOnlyProxy was attacked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute",
    [
        # Repository writes that are NOT tier-1 operations.
        "create_volume_group",
        "add_cartridge",
        "add_barcode_to_volume_group",
        "delete_file_record",
        "update_job_state",
        # Repository reads: the facade is not a second read proxy either.
        "list_volume_groups",
        "get_cartridge",
        "session",
        "_session",
        # The escapes that broke the first ReadOnlyProxy.
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
def test_setup_facade_exposes_nothing_but_its_operations(app_context: Any, attribute: str) -> None:
    """Mutation: widen SETUP_OPERATION_NAMES or drop the __getattribute__ check -> fails."""
    facade = setup_facade(app_context.catalog)
    with pytest.raises(SetupFacadeViolationError):
        getattr(facade, attribute)


def test_setup_facade_serves_its_operations(app_context: Any) -> None:
    """The guard must not be vacuous."""
    facade = setup_facade(app_context.catalog)
    assert facade.known_volume_groups() == {"volumeGroups": []}


def test_setup_facade_cannot_be_rebound_or_re_initialised(app_context: Any) -> None:
    facade = setup_facade(app_context.catalog)
    with pytest.raises(SetupFacadeViolationError):
        facade.new_volume_group = lambda **kwargs: None  # type: ignore[misc]
    with pytest.raises(SetupFacadeViolationError):
        del facade.new_volume_group  # type: ignore[misc]
    with pytest.raises(SetupFacadeViolationError):
        facade.__init__(app_context.catalog)
    # The unbound form bypasses instance attribute lookup entirely, so the state map
    # refuses a second binding instead.
    with pytest.raises(ReadOnlyViolationError):
        AllowlistProxy.__init__(facade, app_context.catalog, frozenset({"anything"}), "setup")
    with pytest.raises(SetupFacadeViolationError):
        getattr(facade, "anything")  # noqa: B009 - the lookup IS the test


def test_setup_facade_cannot_be_copied_or_pickled(app_context: Any) -> None:
    facade = setup_facade(app_context.catalog)
    with pytest.raises(SetupFacadeViolationError):
        copy.copy(facade)
    with pytest.raises(SetupFacadeViolationError):
        pickle.dumps(facade)


def test_subclassing_the_facade_does_not_widen_it(app_context: Any) -> None:
    """A subclass inherits the guard; it does not get a fresh ``object`` surface."""
    subclass = type("Sneaky", (SetupFacade,), {})
    facade = subclass(app_context.catalog)
    with pytest.raises(SetupFacadeViolationError):
        getattr(facade, "create_volume_group")  # noqa: B009 - the lookup IS the test
    assert facade.known_volume_groups() == {"volumeGroups": []}


@pytest.mark.parametrize(
    "attribute", ["__closure__", "__self__", "__func__", "__class__", "__dict__", "args", "func"]
)
def test_a_bound_setup_operation_leaks_no_reference_to_the_catalog(
    app_context: Any, attribute: str
) -> None:
    """The subtle escape: a closure or bound method hands back its receiver.

    ``fn.__closure__[0].cell_contents`` and ``method.__self__`` are both routes to
    the live CatalogRepository for anyone holding a callable the facade returned.
    Operations are therefore sealed objects, not closures.
    """
    operation = setup_facade(app_context.catalog).known_volume_groups
    with pytest.raises(SetupFacadeViolationError):
        getattr(operation, attribute)


def test_the_facade_never_holds_the_library_backend(app_context: Any) -> None:
    """Tier 1 is catalog-only: there is no media-moving object behind the facade."""
    facade = setup_facade(app_context.catalog)
    for attribute in ("library", "inventory", "load", "unload", "move", "eject"):
        with pytest.raises(SetupFacadeViolationError):
            getattr(facade, attribute)


def test_read_tool_context_has_no_route_to_the_facade(app_context: Any) -> None:
    """The read-only tools and the write facade live in separate containers."""
    context = build_context(
        config=assistant_config(),
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url="sqlite:///x.db",
    )
    assert not hasattr(context, "setup")
    with pytest.raises(ReadOnlyViolationError):
        getattr(context.catalog, "create_volume_group")  # noqa: B009 - the lookup IS the test


# ---------------------------------------------------------------------------
# 8. The setup prompt states the two tiers (text assertions, no guard to remove)
# ---------------------------------------------------------------------------


def test_setup_prompt_keeps_tier_two_propose_only() -> None:
    assert "Tier 1 — you may call these tools:" in SETUP_SYSTEM_PROMPT
    assert "Calling one of those does NOT perform it." in SETUP_SYSTEM_PROMPT
    assert (
        "Tier 2 — everything else, including every command that formats, loads, unloads,\n"
        "moves, ejects, archives, restores or deletes. You have no tool for any of it and\n"
        "you never will."
    ) in SETUP_SYSTEM_PROMPT
    # The destructive flow and the refusal instruction are the same text as the
    # read-only contract: tier 1 softens no gate.
    assert "openblade format dry-run --barcode" in SETUP_SYSTEM_PROMPT
    assert "refuse. Say plainly that you will not" in SETUP_SYSTEM_PROMPT


def test_read_only_prompt_points_setup_execution_at_the_repl() -> None:
    assert "only in the interactive REPL" in SYSTEM_PROMPT
    assert "You are a read-only advisor. You cannot run anything." in SYSTEM_PROMPT
