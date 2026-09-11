"""Tier-1 setup tools: the few actions the assistant may execute, after a yes.

The assistant has two tiers of action:

* **Tier 1 — executable after in-chat confirmation.** Safe, reversible,
  catalog-only writes: create a volume group, add existing tapes to one. Nothing
  touches media or the library hardware. A tier-1 tool call does **not** run on
  arrival: the session turns it into a :class:`PendingAction`, the REPL prints the
  preview and asks ``[y/N]``, and only an explicit yes executes it.
* **Tier 2 — everything else** (format, load/unload/move, archive, restore,
  delete). Propose-only, exactly as before. There is no tool for any of it.

Three independent guards keep tier 1 from growing by accident:

1. :data:`SETUP_TOOL_NAMES` — a fail-closed allowlist. A tool whose name is not on
   it raises :class:`SetupRegistryViolationError` at registry-build time.
2. :data:`DESTRUCTIVE_VERBS` — a denylist that wins over the allowlist. A name
   containing any of those verbs is refused even if someone also added it to the
   allowlist, so widening tier 1 to a destructive operation takes two deliberate
   edits in two places plus a test change, not one.
3. :mod:`openblade.assistant.setup_facade` — the tools cannot reach a repository
   method at all; they see two named operations and nothing else.

The denylist is a plain substring test on the lowercased name, which is stricter
than it first looks: ``remove_tapes_from_volume_group`` is rejected because it
contains ``move``. That is intentional. Removal is not implemented (the catalog
layer has no such method), and if it is added later it must be named for what it
does to the catalog — ``detach_tapes_from_volume_group`` — rather than borrowing a
verb that also describes moving physical media.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openblade.assistant.errors import (
    SetupRegistryViolationError,
    ToolNotFoundError,
)
from openblade.assistant.setup_facade import SetupFacade, clean_barcodes, clean_name

JSONDict = dict[str, Any]

logger = logging.getLogger("openblade.assistant.setup")

# ---------------------------------------------------------------------------
# The two guards on what may ever be a tier-1 tool
# ---------------------------------------------------------------------------

SETUP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "create_volume_group",
        "add_tapes_to_volume_group",
    }
)

# Verbs that mark an operation as destructive or media-moving. No tool name may
# contain one, allowlisted or not.
DESTRUCTIVE_VERBS: tuple[str, ...] = (
    "format",
    "load",
    "unload",
    "move",
    "eject",
    "delete",
    "erase",
    "wipe",
    "restore",
    "archive",
    "write",
)


def reject_destructive_name(name: str) -> None:
    """Raise if ``name`` contains a destructive verb. The denylist wins, always."""
    lowered = name.lower()
    for verb in DESTRUCTIVE_VERBS:
        if verb in lowered:
            raise SetupRegistryViolationError(
                f"Tool {name!r} contains the destructive verb {verb!r}. The assistant "
                "executes only safe, reversible, catalog-only setup actions; anything "
                "that formats, moves media, deletes or writes stays propose-only."
            )


def _assert_allowlist_is_clean() -> None:
    """The denylist also polices the allowlist itself, at import time.

    Without this, adding ``delete_volume_group`` to ``SETUP_TOOL_NAMES`` would only
    fail once someone wrote the tool. It fails on the edit instead.
    """
    for name in sorted(SETUP_TOOL_NAMES):
        reject_destructive_name(name)


_assert_allowlist_is_clean()


# ---------------------------------------------------------------------------
# Pending actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingAction:
    """A tier-1 action awaiting the operator's yes.

    ``preview`` names the objects in plain language ("Create volume group 'photos'
    containing tapes PH000001, PH000002") because that sentence is the whole basis
    on which the operator answers. ``arguments`` are already normalized, so the
    preview, the decline cache and the audit line all describe the same action.
    """

    tool: str
    arguments: dict[str, Any]
    preview: str

    @property
    def key(self) -> str:
        """Identity of this action, for the repeat-proposal cap."""
        return f"{self.tool}:{json.dumps(self.arguments, sort_keys=True, default=str)}"


ConfirmCallback = Callable[[PendingAction], bool]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupTool:
    """One tier-1 tool: normalize, plan (validate + preview), then execute."""

    name: str
    description: str
    parameters: JSONDict
    normalize: Callable[[Mapping[str, Any]], dict[str, Any]]
    plan: Callable[[SetupFacade, dict[str, Any]], JSONDict]
    describe: Callable[[dict[str, Any], JSONDict], str]
    apply: Callable[[SetupFacade, dict[str, Any]], JSONDict]

    def schema(self) -> JSONDict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _normalize_create(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": clean_name(arguments.get("name"))}


def _plan_create(facade: SetupFacade, arguments: dict[str, Any]) -> JSONDict:
    # Annotated locals: the facade resolves through a sealed call, so its return
    # type is Any at the boundary and mypy needs the shape stated once, here.
    result: JSONDict = facade.plan_new_volume_group(name=arguments["name"])
    return result


def _describe_create(arguments: dict[str, Any], plan: JSONDict) -> str:
    existing = plan.get("existingVolumeGroups") or []
    suffix = f" (you have {len(existing)} already)" if existing else " (your first pool)"
    return f"Create volume group {arguments['name']!r}{suffix}."


def _execute_create(facade: SetupFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.new_volume_group(name=arguments["name"])
    return result


def _normalize_add(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_name(arguments.get("name")),
        "barcodes": list(clean_barcodes(arguments.get("barcodes"))),
    }


def _plan_add(facade: SetupFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_attach_tapes(
        name=arguments["name"], barcodes=arguments["barcodes"]
    )
    return result


def _describe_add(arguments: dict[str, Any], plan: JSONDict) -> str:
    to_add = plan.get("toAdd") or []
    already = plan.get("alreadyPresent") or []
    if not to_add:
        return (
            f"Add no tapes to volume group {arguments['name']!r}: "
            f"{', '.join(already)} are already in it."
        )
    sentence = f"Add tape(s) {', '.join(to_add)} to volume group {arguments['name']!r}"
    if already:
        sentence += f" ({', '.join(already)} already in it)"
    return sentence + "."


def _execute_add(facade: SetupFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.attach_tapes(
        name=arguments["name"], barcodes=arguments["barcodes"]
    )
    return result


def _tool_definitions() -> list[SetupTool]:
    return [
        SetupTool(
            name="create_volume_group",
            description=(
                "Create an empty volume group (pool) in the catalog. The operator is "
                "asked to confirm before this runs. Creates no tapes and touches no "
                "media."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the new volume group."}
                },
                "required": ["name"],
            },
            normalize=_normalize_create,
            plan=_plan_create,
            describe=_describe_create,
            apply=_execute_create,
        ),
        SetupTool(
            name="add_tapes_to_volume_group",
            description=(
                "Put existing cartridges into an existing volume group. The operator "
                "is asked to confirm before this runs. Every barcode must already be "
                "known to the library and not already in another pool; otherwise the "
                "action is refused with the candidates it could have meant."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Existing volume group name."},
                    "barcodes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Barcodes of tapes that already exist in this library.",
                    },
                },
                "required": ["name", "barcodes"],
            },
            normalize=_normalize_add,
            plan=_plan_add,
            describe=_describe_add,
            apply=_execute_add,
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SetupToolRegistry:
    """Name -> :class:`SetupTool`, validated against allowlist *and* denylist."""

    def __init__(self, tools: list[SetupTool]) -> None:
        by_name: dict[str, SetupTool] = {}
        for tool in tools:
            # Denylist first: it wins even over an allowlisted name.
            reject_destructive_name(tool.name)
            if tool.name not in SETUP_TOOL_NAMES:
                raise SetupRegistryViolationError(
                    f"Tool {tool.name!r} is not on the assistant setup allowlist. The "
                    "assistant executes only safe, reversible, catalog-only actions; "
                    "add the name to SETUP_TOOL_NAMES only after confirming the tool "
                    "performs no media, hardware or destructive operation."
                )
            if tool.name in by_name:
                raise SetupRegistryViolationError(f"Duplicate setup tool {tool.name!r}")
            by_name[tool.name] = tool
        self._tools = by_name

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get(self, name: str) -> SetupTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"Unknown setup tool {name!r}") from None

    def schemas(self) -> list[JSONDict]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def plan(self, name: str, facade: SetupFacade, arguments: Mapping[str, Any]) -> PendingAction:
        """Validate the call against live state and build the confirmation preview.

        Raises :class:`~openblade.assistant.errors.SetupRefusedError` if the targets
        are not unambiguous — so the operator is never asked to confirm an action
        that would then have to guess.
        """
        tool = self.get(name)
        normalized = tool.normalize(arguments)
        preview = tool.describe(normalized, tool.plan(facade, normalized))
        return PendingAction(tool=name, arguments=normalized, preview=preview)

    def perform(self, action: PendingAction, facade: SetupFacade) -> JSONDict:
        """Run a confirmed action and record one audit line.

        The action is re-validated inside the facade; this is not a second gate, it
        is the same gate applied to the state that exists at write time.
        """
        tool = self.get(action.tool)
        result = tool.apply(facade, action.arguments)
        log_action(action, outcome="executed", detail=result)
        return result


def build_setup_registry(extra: list[SetupTool] | None = None) -> SetupToolRegistry:
    """Build the tier-1 registry.

    ``extra`` exists so tests can prove the allowlist and denylist guards fire;
    production code passes nothing.
    """
    return SetupToolRegistry([*_tool_definitions(), *(extra or [])])


def log_action(action: PendingAction, *, outcome: str, detail: Mapping[str, Any] | None = None) -> None:
    """One structured line per tier-1 decision.

    ``json.dumps`` is what sanitizes here: it escapes CR/LF, so a model-supplied
    name cannot forge a second log line. The catalog has no events table that the
    API uses for volume-group changes, so this log is the audit trail — inventing a
    schema for it is not this feature's business.
    """
    logger.info(
        "actor=assistant tool=%s outcome=%s args=%s result=%s",
        action.tool,
        outcome,
        json.dumps(action.arguments, sort_keys=True, default=str),
        json.dumps(detail or {}, sort_keys=True, default=str),
    )
