"""The assistant's safety line: it reads, explains, and proposes.

The things it may *do* are a tier-1 setup action the operator confirmed with a yes
(create a volume group, add tapes to one) and a tier-2 media action the operator
confirmed *strongly* (load, unload, move, archive, restore, format). These are the
regression tests for both halves of that guarantee: everything else is unreachable,
the tier-1 part is unreachable without a yes, and the tier-2 part is unreachable
without a confirmation matching its consequence — a typed barcode for a format, a
typed word for an overwriting restore.

The structural guards are mutation-checked, meaning removing the guard makes a
named test fail (verified, not assumed):

* the read-only registry allowlist and the ReadOnlyProxy attribute allowlist
  (sections 1-2; ``test_registry_guard_rejects_a_mutating_tool`` mutates inline);
* the write-path AST scan (section 5, ``test_write_path_scan_catches_a_rogue_module``);
* the setup allowlist AND the destructive-verb denylist (section 6 — the denylist
  case widens the allowlist on purpose, so only the denylist can be what fails it);
* the facade's attribute allowlist (section 7);
* the tier-2 registry boundary, the media facade's attribute allowlist, and the
  authorization re-check that stands between a media tool and execution
  (sections 9-11).

Sections 4 and 8 are different and are labelled as such: the prompt tests assert
the text of an instruction, and there is no guard to remove. They catch a prompt
edit that drops the safety contract, nothing more -- the prompt is guidance, and
:mod:`openblade.assistant.readonly` is what actually makes execution impossible.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import gc
import json
import pickle
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
import pytest

from openblade.assistant.errors import (
    MediaFacadeViolationError,
    MediaNotAuthorizedError,
    MediaRegistryViolationError,
    ReadOnlyViolationError,
    SetupFacadeViolationError,
    SetupRegistryViolationError,
    ToolRegistryViolationError,
)
from openblade.assistant.media_facade import MediaFacade, media_bundle
from openblade.assistant.media_tools import (
    MEDIA_TOOL_NAMES,
    ConfirmationGrade,
    MediaAuthorization,
    MediaTool,
    build_media_registry,
)
from openblade.assistant.prompts import (
    MEDIA_SYSTEM_PROMPT,
    SETUP_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    system_message,
)
from openblade.assistant.provider import OllamaClient, ToolCall
from openblade.assistant.readonly import (
    CATALOG_READ_METHODS,
    INVENTORY_READ_METHODS,
    AllowlistProxy,
    read_only_catalog,
    read_only_inventory,
)
from openblade.assistant.session import AssistantSession
from openblade.assistant.setup_facade import SetupFacade, setup_facade
from openblade.assistant.setup_tools import (
    DESTRUCTIVE_VERBS,
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
from tests.assistant_support import assistant_config, media_facade_for

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
        "media_facade.py",
        "media_tools.py",
        "setup_facade.py",
        "setup_tools.py",
        "tools.py",
    }
)

WRITE_PATH_OWNER = "setup_facade.py"

# The one module allowed to reach the tape orchestrator and the media services.
MEDIA_PATH_OWNER = "media_facade.py"


def _catalog_write_methods() -> frozenset[str]:
    """Every ``CatalogRepository`` method that is not a permitted read.

    Derived, not hand-listed. A hand-written set of ten covered a fifth of the
    repository's write surface and would have gone stale the moment someone added
    a method — the scan would have kept passing while covering less. Anything
    public on the repository that is not in ``CATALOG_READ_METHODS`` is treated as
    a write for the purpose of "only the facade may name it", which errs strict.
    """
    from openblade.catalog.repository import CatalogRepository

    public = {
        name
        for name in dir(CatalogRepository)
        if not name.startswith("__") and callable(getattr(CatalogRepository, name, None))
    }
    return frozenset(public - CATALOG_READ_METHODS)


CATALOG_WRITE_METHODS = _catalog_write_methods()


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
    """No `session.commit()`, `flush()`, `rollback()` or `execute()` anywhere in
    the package: the assistant never drives a transaction itself.

    Note what this does NOT say any more. The tier-1 facade calls two repository
    methods that commit internally, one per cartridge, so the assistant does cause
    commits — it just never issues one, and has no session to issue it on. The
    consequence (a multi-barcode action can partially apply) is handled explicitly
    by SetupPartialWriteError rather than hidden behind this test's name."""
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
    others = [path for path in _assistant_sources() if path.name != WRITE_PATH_OWNER]
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
    """Tier 1 is catalog-only: nothing media-moving is behind the facade.

    Asserting that ``facade.load`` raises would prove nothing — every unlisted name
    raises. What matters is the *target*: a facade wrapping a LibraryBackend would
    still refuse ``load``, and would still be a disaster. So assert the wrapped
    object is the catalog, by calling an operation that can only work on one, and
    assert the backend the CLI would hand over is not a catalog.
    """
    facade = setup_facade(app_context.catalog)
    assert facade.known_volume_groups() == {"volumeGroups": []}
    backend = app_context.library
    assert not hasattr(backend, "create_volume_group"), (
        "if a LibraryBackend ever grows catalog-shaped methods, this test must be "
        "rewritten: the facade would no longer be provably over the catalog"
    )
    # And the operations the facade exposes do not exist on the backend at all,
    # so it could not be substituted even by mistake.
    for operation in ("list_volume_groups", "get_cartridge"):
        assert not hasattr(backend, operation)


def test_read_tool_context_has_no_route_to_the_facade(app_context: Any) -> None:
    """The read-only tools and the write facade live in separate containers.

    ``not hasattr(context, "setup")`` alone would be true by construction and could
    never fail, so this also walks every field of the context and asserts none of
    them is a SetupFacade — the failure mode that actually matters is someone
    passing the facade in as a new field later.
    """
    context = build_context(
        config=assistant_config(),
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url="sqlite:///x.db",
    )
    assert not hasattr(context, "setup")
    for field in dataclasses.fields(context):
        value = getattr(context, field.name)
        # ``isinstance`` is not usable here: it consults ``__class__`` on a
        # non-match, and every proxy in the context refuses that lookup.
        assert type(value) is not SetupFacade, field.name
        assert not issubclass(type(value), SetupFacade), field.name
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


# ---------------------------------------------------------------------------
# 9. Tier 2: the media registry boundary, and the three registries stay disjoint
# ---------------------------------------------------------------------------


def _rogue_media_tool(name: str) -> MediaTool:
    return MediaTool(
        name=name,
        description="Should never be registrable.",
        parameters={"type": "object", "properties": {}},
        normalize=lambda arguments: {},
        plan=lambda facade, arguments: {},
        describe=lambda arguments, plan: "nope",
        grade=lambda arguments, plan: (ConfirmationGrade.YES_NO, None),
        apply=lambda facade, action: {},
    )


def _rogue_setup_tool(name: str) -> SetupTool:
    return SetupTool(
        name=name,
        description="Should never be registrable.",
        parameters={"type": "object", "properties": {}},
        normalize=lambda arguments: {},
        plan=lambda facade, arguments: {},
        describe=lambda arguments, plan: "nope",
        apply=lambda facade, arguments: {},
    )


def test_media_allowlist_is_the_reviewed_set() -> None:
    """Six operations, spelled out, so widening tier 2 shows up in a diff."""
    assert set(MEDIA_TOOL_NAMES) == {
        "load_tape",
        "unload_drive",
        "move_tape",
        "format_tape",
        "archive_path",
        "restore_path",
    }
    assert build_media_registry().names == MEDIA_TOOL_NAMES


def test_the_three_registries_are_pairwise_disjoint() -> None:
    """Dispatch is by name, so an overlap would make a tier boundary ambiguous."""
    assert not (MEDIA_TOOL_NAMES & SETUP_TOOL_NAMES)
    assert not (MEDIA_TOOL_NAMES & READ_ONLY_TOOL_NAMES)
    assert not (SETUP_TOOL_NAMES & READ_ONLY_TOOL_NAMES)


@pytest.mark.parametrize("name", sorted(MEDIA_TOOL_NAMES))
def test_a_media_tool_can_never_register_in_the_setup_registry(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tier 2 cannot leak into tier 1, where a bare "y" would confirm it.

    The setup ALLOWLIST is widened to include the media name on purpose — that is
    exactly the mistake this guards against — so the only thing that can refuse it
    is the tier-1 destructive-verb denylist. Remove ``reject_destructive_name``
    from ``SetupToolRegistry.__init__`` and every case here passes, which is how we
    know the denylist and not the allowlist is doing the work.
    """
    monkeypatch.setattr(
        "openblade.assistant.setup_tools.SETUP_TOOL_NAMES",
        frozenset({*SETUP_TOOL_NAMES, name}),
    )
    with pytest.raises(SetupRegistryViolationError) as excinfo:
        build_setup_registry([_rogue_setup_tool(name)])
    assert "destructive verb" in str(excinfo.value)


@pytest.mark.parametrize("name", sorted(SETUP_TOOL_NAMES))
def test_a_setup_tool_can_never_register_in_the_media_registry(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the reverse: tier 1 cannot acquire tier-2's machinery by renaming.

    The media ALLOWLIST is widened to include the setup name, so only the explicit
    cross-registry exclusion in ``MediaToolRegistry.__init__`` can refuse it.
    """
    monkeypatch.setattr(
        "openblade.assistant.media_tools.MEDIA_TOOL_NAMES",
        frozenset({*MEDIA_TOOL_NAMES, name}),
    )
    with pytest.raises(MediaRegistryViolationError) as excinfo:
        build_media_registry([_rogue_media_tool(name)])
    assert "tier-1 setup tool" in str(excinfo.value)


@pytest.mark.parametrize("name", sorted(READ_ONLY_TOOL_NAMES))
def test_a_read_tool_name_can_never_register_in_the_media_registry(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A media tool must not shadow a read tool and silently become confirmable."""
    monkeypatch.setattr(
        "openblade.assistant.media_tools.MEDIA_TOOL_NAMES",
        frozenset({*MEDIA_TOOL_NAMES, name}),
    )
    with pytest.raises(MediaRegistryViolationError):
        build_media_registry([_rogue_media_tool(name)])


def test_the_tier_one_denylist_was_not_weakened() -> None:
    """Tier 2 exists; the tier-1 denylist is untouched.

    Spelled out because the tempting way to add media tools would have been to
    delete a verb from this tuple. Every one of these is still refused for tier 1.
    """
    for verb in (
        "format",
        "delete",
        "erase",
        "wipe",
        "purge",
        "destroy",
        "load",
        "unload",
        "move",
        "eject",
        "import",
        "export",
        "mount",
        "restore",
        "archive",
        "write",
        "revoke",
        "token",
        "rename",
    ):
        assert verb in DESTRUCTIVE_VERBS
        with pytest.raises(SetupRegistryViolationError):
            reject_destructive_name(f"do_{verb}_thing")


def test_only_the_media_facade_reaches_the_orchestrator_or_the_services() -> None:
    """The tier-2 acting path lives in exactly one file, and the scan proves it.

    ``openblade.nas`` is where the tape orchestrator lives — the thing that moves
    media. Importing it anywhere else in the package would route round the media
    registry and its confirmation grades.
    """
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in _assistant_sources()
        if path.name != MEDIA_PATH_OWNER
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("openblade.nas"))
        or (
            isinstance(node, ast.Import)
            and any(alias.name.startswith("openblade.nas") for alias in node.names)
        )
    ]
    assert offenders == []
    # And the guard is not vacuous: the media facade itself does import it.
    facade_source = (ASSISTANT_DIR / MEDIA_PATH_OWNER).read_text(encoding="utf-8")
    assert "from openblade.nas.tape_orchestrator import" in facade_source


# The symbols that actually move media. Naming any of them is performing tape I/O.
MEDIA_ACTING_SYMBOLS = frozenset({"execute_tape_request", "TapeOpRequest", "TapeOpType"})

# The services the facade drives. ``__init__.py`` is the composition root and is
# allowed to *wire* them; nobody else may name them, and wiring is not calling.
MEDIA_SERVICE_SYMBOLS = frozenset({"format_service", "archive_service", "restore_service"})


def _symbol_offenders(paths: Iterable[Path], symbols: frozenset[str]) -> list[str]:
    """Files naming one of ``symbols``, as ``name:line`` strings."""
    offenders: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            named = (isinstance(node, ast.Attribute) and node.attr in symbols) or (
                isinstance(node, ast.Name) and node.id in symbols
            )
            if named:
                offenders.append(f"{path.name}:{node.lineno}")
    return offenders


def test_no_other_module_names_a_media_acting_symbol() -> None:
    """Tape I/O lives in exactly one file, and the scan proves it."""
    others = [path for path in _assistant_sources() if path.name != MEDIA_PATH_OWNER]
    assert _symbol_offenders(others, MEDIA_ACTING_SYMBOLS) == []
    # And the guard is not vacuous: the media facade itself does name them.
    assert _symbol_offenders([ASSISTANT_DIR / MEDIA_PATH_OWNER], MEDIA_ACTING_SYMBOLS)


def test_only_the_facade_and_the_composition_root_name_the_media_services() -> None:
    """``__init__.py`` may hand the services to the bundle; nothing else sees them.

    The distinction matters: the composition root wires objects together once, at
    session construction, under the ``confirm_media is not None`` condition that
    makes one-shot mode read-only. A *tool body* naming ``archive_service`` would
    be a second, unconfirmed path to the same write.
    """
    others = [
        path
        for path in _assistant_sources()
        if path.name not in {MEDIA_PATH_OWNER, "__init__.py"}
    ]
    assert _symbol_offenders(others, MEDIA_SERVICE_SYMBOLS) == []
    assert _symbol_offenders([ASSISTANT_DIR / MEDIA_PATH_OWNER], MEDIA_SERVICE_SYMBOLS)


def test_media_scan_catches_a_rogue_module(tmp_path: Path) -> None:
    """THE MUTATION CHECK for the scan above."""
    rogue = tmp_path / "rogue.py"
    rogue.write_text(
        "def sneak(ctx):\n    return execute_tape_request(ctx, None, None, None)\n",
        encoding="utf-8",
    )
    assert _symbol_offenders([rogue], MEDIA_ACTING_SYMBOLS) == ["rogue.py:2"]


# ---------------------------------------------------------------------------
# 10. The media facade, attacked the way both other proxies were attacked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attribute",
    [
        # The live objects the bundle holds. Reaching any of them would hand a tool
        # the unguarded hardware path the whole tier exists to wrap.
        "library",
        "ltfs",
        "catalog_repo",
        "catalog",
        "inventory",
        "format_service",
        "archive_service",
        "restore_service",
        "drive_serials",
        # Backend methods, in case the bundle were ever swapped for a backend.
        "load",
        "unload",
        "move",
        "format",
        "mount",
        "inventory_",
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
def test_media_facade_exposes_nothing_but_its_operations(app_context: Any, attribute: str) -> None:
    """Mutation: widen MEDIA_OPERATION_NAMES or drop the __getattribute__ check -> fails."""
    facade = media_facade_for(app_context)
    with pytest.raises(MediaFacadeViolationError):
        getattr(facade, attribute)


def test_media_facade_serves_its_operations(app_context: Any) -> None:
    """The guard must not be vacuous."""
    facade = media_facade_for(app_context)
    state = facade.library_state()
    assert state["drives"] and state["barcodes"]


def test_media_facade_cannot_be_rebound_or_re_initialised(app_context: Any) -> None:
    facade = media_facade_for(app_context)
    with pytest.raises(MediaFacadeViolationError):
        facade.load_tape = lambda **kwargs: None  # type: ignore[misc]
    with pytest.raises(MediaFacadeViolationError):
        del facade.load_tape  # type: ignore[misc]
    with pytest.raises(MediaFacadeViolationError):
        facade.__init__(app_context)
    # The unbound form bypasses instance attribute lookup entirely, so the state
    # map refuses a second binding instead.
    with pytest.raises(ReadOnlyViolationError):
        AllowlistProxy.__init__(facade, app_context, frozenset({"anything"}), "media")
    with pytest.raises(MediaFacadeViolationError):
        getattr(facade, "anything")  # noqa: B009 - the lookup IS the test


def test_media_facade_cannot_be_copied_or_pickled(app_context: Any) -> None:
    facade = media_facade_for(app_context)
    with pytest.raises(MediaFacadeViolationError):
        copy.copy(facade)
    with pytest.raises(MediaFacadeViolationError):
        pickle.dumps(facade)


def test_subclassing_the_media_facade_does_not_widen_it(app_context: Any) -> None:
    subclass = type("Sneaky", (MediaFacade,), {})
    facade = subclass(
        media_bundle(
            catalog=read_only_catalog(app_context.catalog),
            inventory=read_only_inventory(app_context.inventory_service),
            catalog_repo=app_context.catalog,
            library=app_context.library,
            ltfs=app_context.ltfs,
            format_service=app_context.format_service,
            archive_service=app_context.archive_service,
            restore_service=app_context.restore_service,
        )
    )
    with pytest.raises(MediaFacadeViolationError):
        getattr(facade, "library")  # noqa: B009 - the lookup IS the test
    assert facade.library_state()["drives"]


@pytest.mark.parametrize(
    "attribute", ["__closure__", "__self__", "__func__", "__class__", "__dict__", "args", "func"]
)
def test_a_bound_media_operation_leaks_no_reference_to_the_hardware(
    app_context: Any, attribute: str
) -> None:
    """The subtle escape: a closure hands back the bundle, and the bundle holds the
    library backend. Operations are therefore sealed objects, not closures."""
    operation = media_facade_for(app_context).load_tape
    with pytest.raises(MediaFacadeViolationError):
        getattr(operation, attribute)


def test_the_read_tool_context_has_no_route_to_the_media_facade(app_context: Any) -> None:
    """The read-only tools and the media facade live in separate containers."""
    context = build_context(
        config=assistant_config(),
        catalog=app_context.catalog,
        inventory_service=app_context.inventory_service,
        backend="mock",
        real_hardware_enabled=False,
        db_url="sqlite:///x.db",
    )
    assert not hasattr(context, "media")
    for field in dataclasses.fields(context):
        value = getattr(context, field.name)
        # ``isinstance`` is not usable here: it consults ``__class__`` on a
        # non-match, and every proxy in the context refuses that lookup.
        assert type(value) is not MediaFacade, field.name
        assert not issubclass(type(value), MediaFacade), field.name


def test_the_setup_facade_is_still_catalog_only(app_context: Any) -> None:
    """Tier 2 exists now; tier 1 did not quietly grow a hardware path with it."""
    facade = setup_facade(app_context.catalog)
    for name in ("load_tape", "format_tape", "archive_path", "library", "ltfs"):
        with pytest.raises(SetupFacadeViolationError):
            getattr(facade, name)


# ---------------------------------------------------------------------------
# 11. A tier-2 tool cannot execute without a matching strong confirmation
# ---------------------------------------------------------------------------


def _planned(app_context: Any, name: str, **arguments: Any) -> Any:
    """Build a registry + facade and plan one action against the live context."""
    registry = build_media_registry()
    facade = media_facade_for(app_context)
    return registry, facade, registry.plan(name, facade, arguments)


def test_a_media_action_cannot_run_without_an_authorization(app_context: Any) -> None:
    """MUTATION CHECK: delete the ``authorization is None`` check in perform -> fails."""
    registry, facade, action = _planned(app_context, "load_tape", barcode="VOL001L9", drive=0)
    with pytest.raises(MediaNotAuthorizedError):
        registry.perform(action, facade, None)
    assert app_context.library.inventory().drives[0].barcode is None


def test_a_format_cannot_be_smuggled_through_the_yes_no_grade(app_context: Any) -> None:
    """The attack: a forged YES_NO authorization carrying "y" for a TYPED action.

    Two independent checks refuse this — the grade comparison and the response
    re-verification — so it is a *scenario* test, not a mutation check for either
    one. The two below isolate them. What it does prove end to end is that nothing
    ran and, crucially, that the safety token was not consumed: a failed smuggling
    attempt must not burn the operator's live authorization.
    """
    registry, facade, action = _planned(app_context, "format_tape", barcode="VOL001L9")
    forged = MediaAuthorization(
        action_key=action.key, grade=ConfirmationGrade.YES_NO, response="y"
    )
    with pytest.raises(MediaNotAuthorizedError):
        registry.perform(action, facade, forged)
    assert app_context.catalog.get_safety_token(action.token) is not None, "not consumed"


def test_an_authorization_minted_under_a_weaker_grade_is_refused(app_context: Any) -> None:
    """MUTATION CHECK for the grade comparison, isolated.

    The response here — the barcode — *does* satisfy the action's real grade, so
    the response re-verification passes and only the grade comparison can refuse
    it. Delete that comparison and this test fails while everything else stays
    green, which is what makes the check demonstrably load-bearing rather than
    decorative defence in depth.
    """
    registry, facade, action = _planned(app_context, "format_tape", barcode="VOL001L9")
    forged = MediaAuthorization(
        action_key=action.key, grade=ConfirmationGrade.YES_NO, response="VOL001L9"
    )
    with pytest.raises(MediaNotAuthorizedError) as excinfo:
        registry.perform(action, facade, forged)
    assert "requires a typed confirmation" in str(excinfo.value)
    assert app_context.catalog.get_safety_token(action.token) is not None


def test_an_authorization_for_one_action_does_not_authorize_another(app_context: Any) -> None:
    """MUTATION CHECK for the action-key comparison, isolated.

    Both actions are the same tool at the same grade and the response is a valid
    "y", so the grade check and the response check both pass: only the key
    comparison stands between a yes given for VOL001L9 and a robot arm moving
    VOL002L9. Delete it and this fails.
    """
    registry, facade, approved = _planned(app_context, "load_tape", barcode="VOL001L9", drive=0)
    authorization = registry.authorize(approved, "y")
    other = registry.plan("load_tape", facade, {"barcode": "VOL002L9", "drive": 0})
    with pytest.raises(MediaNotAuthorizedError) as excinfo:
        registry.perform(other, facade, authorization)
    assert "different action" in str(excinfo.value)
    assert app_context.library.inventory().drives[0].barcode is None, "nothing moved"


def test_replaying_a_harmless_yes_against_a_format_is_refused(app_context: Any) -> None:
    """The headline attack, kept as a scenario: a yes for a load, replayed at a format."""
    registry, facade, harmless = _planned(app_context, "load_tape", barcode="VOL001L9", drive=0)
    authorization = registry.authorize(harmless, "y")
    destructive = registry.plan("format_tape", facade, {"barcode": "VOL002L9"})
    with pytest.raises(MediaNotAuthorizedError):
        registry.perform(destructive, facade, authorization)


def test_a_declined_action_cannot_be_replayed_from_the_decline_cache(
    app_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal is a refusal on every repeat; the cache answers, it never executes.

    The attack: propose a format, have the operator decline, then propose the exact
    same call again hoping the cached answer short-circuits into an execution.
    """
    asked: list[str] = []

    def refuse(action: Any) -> str:
        asked.append(action.tool)
        return "n"

    session = AssistantSession(
        client=OllamaClient(assistant_config(), client=httpx.Client()),
        registry=build_registry(),
        context=build_context(
            config=assistant_config(),
            catalog=app_context.catalog,
            inventory_service=app_context.inventory_service,
            backend="mock",
            real_hardware_enabled=False,
            db_url="sqlite:///x.db",
        ),
        config=assistant_config(),
        media_registry=build_media_registry(),
        media=media_facade_for(app_context),
        confirm_media=refuse,
    )
    call = ToolCall(name="format_tape", arguments={"barcode": "VOL001L9"})
    first = json.loads(session._run_media_tool(call))
    second = json.loads(session._run_media_tool(call))
    assert first["status"] == "declined_by_operator"
    assert second["status"] == "declined_by_operator"
    assert second["repeatedProposal"] is True
    assert asked == ["format_tape"], "the operator is asked once, not nagged"
    assert session.executed_this_turn == ()


def test_one_shot_mode_offers_neither_tier(app_context: Any) -> None:
    """MUTATION CHECK: make ``media_enabled`` unconditional -> this fails.

    One-shot has nowhere to ask a human, so the model must not even be told the
    executing tools exist.
    """
    session = AssistantSession(
        client=OllamaClient(assistant_config(), client=httpx.Client()),
        registry=build_registry(),
        context=build_context(
            config=assistant_config(),
            catalog=app_context.catalog,
            inventory_service=app_context.inventory_service,
            backend="mock",
            real_hardware_enabled=False,
            db_url="sqlite:///x.db",
        ),
        config=assistant_config(),
    )
    assert session.setup_enabled is False
    assert session.media_enabled is False
    offered = {schema["function"]["name"] for schema in session._schemas()}
    assert offered == set(READ_ONLY_TOOL_NAMES)
    assert not (offered & MEDIA_TOOL_NAMES)
    assert not (offered & SETUP_TOOL_NAMES)


@pytest.mark.parametrize(
    "missing", ["media_registry", "media", "confirm_media"]
)
def test_a_half_wired_media_session_is_read_only(app_context: Any, missing: str) -> None:
    """Missing any one of the three parts means tier 2 is off, not unconfirmed."""
    parts: dict[str, Any] = {
        "media_registry": build_media_registry(),
        "media": media_facade_for(app_context),
        "confirm_media": lambda action: "y",
    }
    parts[missing] = None
    session = AssistantSession(
        client=OllamaClient(assistant_config(), client=httpx.Client()),
        registry=build_registry(),
        context=build_context(
            config=assistant_config(),
            catalog=app_context.catalog,
            inventory_service=app_context.inventory_service,
            backend="mock",
            real_hardware_enabled=False,
            db_url="sqlite:///x.db",
        ),
        config=assistant_config(),
        **parts,
    )
    assert session.media_enabled is False
    offered = {schema["function"]["name"] for schema in session._schemas()}
    assert not (offered & MEDIA_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 12. The tier-2 prompt states the confirmation contract (text, not a guard)
# ---------------------------------------------------------------------------


def test_media_prompt_states_the_strong_confirmation_rule() -> None:
    assert "Tier 2 — media and robotics, confirmed with a STRONG confirmation:" in (
        MEDIA_SYSTEM_PROMPT
    )
    assert (
        "the operator must TYPE a\nspecific word — a yes is not accepted, and you must "
        "never tell them a yes will do."
    ) in MEDIA_SYSTEM_PROMPT
    assert "Calling any of these does NOT perform it." in MEDIA_SYSTEM_PROMPT


def test_media_prompt_keeps_the_two_phase_flow_and_the_refusals() -> None:
    """Tier 2 executes the format flow; it does not soften a single gate."""
    assert "openblade format dry-run --barcode" in MEDIA_SYSTEM_PROMPT
    assert "one-time safety token" in MEDIA_SYSTEM_PROMPT
    assert (
        "If asked how to skip, disable, forge, patch out or otherwise bypass a safety gate"
    ) in MEDIA_SYSTEM_PROMPT
    assert "A tape is never unloaded while LTFS is mounted or dirty" in MEDIA_SYSTEM_PROMPT


def test_the_media_prompt_is_only_sent_when_tier_two_is_live() -> None:
    assert system_message()["content"] == SYSTEM_PROMPT
    assert system_message(setup_enabled=True)["content"] == SETUP_SYSTEM_PROMPT
    assert system_message(setup_enabled=True, media_enabled=True)["content"] == (
        MEDIA_SYSTEM_PROMPT
    )


# ---------------------------------------------------------------------------
# 13. Attacks found by attacking this boundary rather than by a failing test
# ---------------------------------------------------------------------------


def test_mutating_a_pending_action_after_authorizing_it_refuses(app_context: Any) -> None:
    """The attack: get a yes for a harmless target, then swap the target.

    ``PendingMediaAction`` is frozen, but ``arguments`` is a plain dict and so is
    mutable in place. The defence is that ``key`` is derived from ``arguments``, so
    editing them invalidates the authorization rather than re-pointing it — which
    only works because ``perform`` re-checks the key instead of trusting the object.
    """
    registry, facade, action = _planned(app_context, "load_tape", barcode="VOL001L9", drive=0)
    authorization = registry.authorize(action, "y")
    action.arguments["barcode"] = "VOL002L9"
    with pytest.raises(MediaNotAuthorizedError):
        registry.perform(action, facade, authorization)
    inventory = app_context.library.inventory()
    assert inventory.drives[0].barcode is None, "nothing moved"


def test_a_model_supplied_token_is_ignored(app_context: Any) -> None:
    """The attack: pass ``token`` as a tool argument and skip the dry run.

    Normalization keeps only the parameters the tool declares, so a model-invented
    token never reaches ``arguments`` — and the token that is used comes from the
    plan, which is the dry run.
    """
    registry, facade, action = _planned(
        app_context, "format_tape", barcode="VOL001L9", token="forged", confirmed=True
    )
    assert set(action.arguments) == {"barcode"}
    assert action.token != "forged"
    assert app_context.catalog.get_safety_token(action.token) is not None


def test_the_facade_holds_no_reachable_instance_state(app_context: Any) -> None:
    """``object.__getattribute__`` bypasses the override — and finds nothing.

    The proxy stores its target in a module-private ``WeakKeyDictionary``, not on
    the instance, so the one lookup path that skips ``__getattribute__`` returns an
    empty dict. (What this does NOT claim: that the bundle is unreachable anywhere
    in the process. A determined ``gc.get_objects()`` walk finds the state map, as
    it does for the tier-1 proxies. The guarantee is "no attribute path from a tool
    body to the hardware", and that is what is tested here and above.)
    """
    facade = media_facade_for(app_context)
    assert object.__getattribute__(facade, "__dict__") == {}
    assert all(
        type(referent).__name__ in {"dict", "type"} for referent in gc.get_referents(facade)
    )
