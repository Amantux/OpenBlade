"""Tier-2 media tools: ask, confirm *strongly*, then do.

Tier 1 (:mod:`openblade.assistant.setup_tools`) is a catalog edit behind a ``y/N``.
Tier 2 is media and robotics, and the difference is the confirmation, not the
plumbing: the operator sees a preview naming the exact objects and the exact
consequence, and answers something the model could not have answered for them.

Three registries, three allowlists, no overlap
----------------------------------------------
:data:`MEDIA_TOOL_NAMES` is this tier's fail-closed allowlist and is enforced by
:class:`MediaToolRegistry` — a tool whose name is not on it raises at registry-build
time. The tier-1 denylist (``DESTRUCTIVE_VERBS``) is untouched and still guards the
*setup* registry, which is why every name here — ``load_tape``, ``unload_drive``,
``move_tape``, ``format_tape``, ``archive_path``, ``restore_path`` — is permanently
unregistrable as a tier-1 tool. The reverse holds too: this registry refuses any
name already claimed by the setup or read-only registries, so the three surfaces
cannot merge by a copy-paste.

Confirmation grades
-------------------
:class:`ConfirmationGrade` is a property of the *tool and its resolved plan*, never
of the model's arguments. There is no path by which a model can select a weaker
grade for an operation:

* ``YES_NO`` — robotics and archive. Nothing is lost; the risk is doing the right
  thing to the wrong cartridge, which a preview naming barcode, slot, drive and
  drive serial is enough to catch.
* ``TYPED`` — format, and a restore that would overwrite an existing file. The
  operator types the barcode (format) or the literal word the preview demands
  (restore). A bare ``y`` is *refused*, not merely insufficient: a reflex answer
  must not be able to erase a cartridge.

The grade is checked twice, and the second check is the one that matters.
:func:`MediaToolRegistry.authorize` turns a typed response into a
:class:`MediaAuthorization`; :func:`MediaToolRegistry.perform` re-verifies that
authorization against the action before it calls anything. A caller that skips
``authorize``, forges an authorization for a different action, or passes ``"y"``
for a ``TYPED`` grade gets :class:`~openblade.assistant.errors.MediaNotAuthorizedError`
and no execution — so the strength of the confirmation does not depend on the REPL
prompt being written correctly.

Ambiguity refuses even when confirmed: planning resolves the target against the
live library, and the executing operation resolves it again inside the write path.
See :mod:`openblade.assistant.media_facade`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openblade.assistant.errors import (
    MediaNotAuthorizedError,
    MediaRefusedError,
    MediaRegistryViolationError,
    ToolNotFoundError,
)
from openblade.assistant.media_facade import (
    OVERWRITE_WORD,
    MediaFacade,
    clean_barcode,
    clean_catalog_path,
    clean_path,
    human_bytes,
    sentence_case,
)
from openblade.assistant.setup_tools import SETUP_TOOL_NAMES, log_action
from openblade.assistant.tools import READ_ONLY_TOOL_NAMES

JSONDict = dict[str, Any]

# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

MEDIA_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "load_tape",
        "unload_drive",
        "move_tape",
        "format_tape",
        "archive_path",
        "restore_path",
    }
)

#: Answers that mean yes for a ``YES_NO`` grade. Anything else — including a bare
#: Enter, "ok", "sure" or EOF — is a no: a confirmation is given, never merely
#: not refused.
YES_ANSWERS: frozenset[str] = frozenset({"y", "yes"})


class ConfirmationGrade(str, Enum):
    """How strongly one action must be confirmed."""

    YES_NO = "yes_no"
    TYPED = "typed"


# ---------------------------------------------------------------------------
# Pending actions and authorizations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingMediaAction:
    """A tier-2 action awaiting the operator's confirmation.

    ``preview`` is the whole basis on which the operator answers, so it names the
    objects ("Load OB0003L8 from slot 3 into drive 1 (serial OBLADE_D02)") and, for
    a destructive action, what is irreversibly lost.

    ``token`` carries the one-time :class:`~openblade.domain.policies.SafetyToken`
    minted by the format dry run. It is deliberately outside ``arguments``: the
    audit line and the decline cache serialize ``arguments``, and a live
    authorization for a destructive operation does not belong in either.
    """

    tool: str
    arguments: dict[str, Any]
    preview: str
    grade: ConfirmationGrade
    plan: JSONDict = field(default_factory=dict, repr=False)
    #: What the operator must type for a ``TYPED`` grade. ``None`` for ``YES_NO``.
    required_response: str | None = None
    token: str | None = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> str:
        """Identity of this action, for the repeat-proposal cap."""
        return f"{self.tool}:{json.dumps(self.arguments, sort_keys=True, default=str)}"

    @property
    def destructive(self) -> bool:
        return self.grade is ConfirmationGrade.TYPED


@dataclass(frozen=True)
class MediaAuthorization:
    """Proof that one specific action was confirmed, carrying what was typed.

    The response travels with the authorization so ``perform`` can re-verify it
    rather than trusting that whoever built this object checked correctly.
    """

    action_key: str
    grade: ConfirmationGrade
    response: str


#: Asks the operator to confirm one tier-2 action, returning what they typed.
#: ``None`` (or anything the grade does not accept) is a refusal. It returns text
#: rather than a bool on purpose: a ``bool`` callback cannot express "typed the
#: barcode", so the typed grade would collapse back into a y/N at the boundary.
MediaConfirmCallback = Callable[[PendingMediaAction], str | None]

#: Called with one line before and after a long synchronous operation.
ProgressCallback = Callable[[str], None]


def verify_response(action: PendingMediaAction, response: str | None) -> bool:
    """Does ``response`` satisfy ``action``'s confirmation grade?

    Fail-closed in every direction: no response, an unknown grade, or a ``TYPED``
    action with no required text all answer False.
    """
    text = (response or "").strip()
    if not text:
        return False
    if action.grade is ConfirmationGrade.YES_NO:
        return text.lower() in YES_ANSWERS
    if action.grade is ConfirmationGrade.TYPED:
        required = (action.required_response or "").strip()
        if not required:
            return False
        if required.lower() in YES_ANSWERS:
            # A required response of "y" would silently downgrade a typed
            # confirmation to a reflex one. Refuse the action rather than honour it.
            return False
        return text.upper() == required.upper()
    return False  # pragma: no cover - the enum has no third member


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

Normalizer = Callable[[Mapping[str, Any]], dict[str, Any]]
Planner = Callable[[MediaFacade, dict[str, Any]], JSONDict]
Describer = Callable[[dict[str, Any], JSONDict], str]
Grader = Callable[[dict[str, Any], JSONDict], tuple[ConfirmationGrade, str | None]]
Applier = Callable[[MediaFacade, PendingMediaAction], JSONDict]


@dataclass(frozen=True)
class MediaTool:
    """One tier-2 tool: normalize, plan, describe, grade, then (after a yes) apply."""

    name: str
    description: str
    parameters: JSONDict
    normalize: Normalizer
    plan: Planner
    describe: Describer
    grade: Grader
    apply: Applier
    #: True when the operation can block for minutes and owns a job row.
    long_running: bool = False

    def schema(self) -> JSONDict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _optional(arguments: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = arguments.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _barcode(arguments: Mapping[str, Any], *names: str, required: bool = True) -> str | None:
    """Normalize a barcode argument to its canonical form, or refuse.

    Normalizing HERE rather than only inside the facade is what makes the decline
    cache work: the cache is keyed on the normalized arguments, so without this
    "VOL001L9", "vol001l9" and " VOL001L9 " are three different actions and an
    operator who declined a format once can be asked about it three more times.
    Confirmation fatigue is the realistic attack on a type-the-barcode gate; an
    adversarial review got four prompts out of one refused action.
    """
    raw = _optional(arguments, *names)
    if raw is None:
        if required:
            raise MediaRefusedError("A tape barcode is required.", code="missing_barcode")
        return None
    return clean_barcode(raw)


def _number(arguments: Mapping[str, Any], *names: str) -> int | None:
    raw = _optional(arguments, *names)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise MediaRefusedError(f"{raw!r} is not a slot or drive number.", code="invalid_number") from None


def _yes_no(_: dict[str, Any], __: JSONDict) -> tuple[ConfirmationGrade, str | None]:
    return ConfirmationGrade.YES_NO, None


# -- load -------------------------------------------------------------------


def _normalize_load(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "barcode": _barcode(arguments, "barcode", "tape"),
        "drive": _number(arguments, "drive", "drive_id", "driveId"),
    }


def _plan_load(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_load_tape(
        barcode=arguments["barcode"], drive=arguments["drive"]
    )
    return result


def _describe_load(_: dict[str, Any], plan: JSONDict) -> str:
    drive = f"drive {plan['driveId']}"
    if plan.get("driveSerial"):
        drive += f" (serial {plan['driveSerial']})"
    chosen = " — chosen because it is free" if plan.get("driveChosenAutomatically") else ""
    return (
        f"Load {plan['barcode']} from slot {plan['slotId']} into {drive}{chosen}.\n"
        f"  Slot {plan['slotId']} becomes empty; the cartridge is not written to and "
        "nothing is mounted."
    )


def _apply_load(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    result: JSONDict = facade.load_tape(
        barcode=action.arguments["barcode"], drive=action.arguments["drive"]
    )
    return result


# -- unload -----------------------------------------------------------------


def _normalize_unload(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        # Not required: "unload drive 1" is a legitimate way to say it, and the
        # facade resolves which cartridge that is and names it back.
        "barcode": _barcode(arguments, "barcode", "tape", required=False),
        "drive": _number(arguments, "drive", "drive_id", "driveId"),
        "slot": _number(arguments, "slot", "slot_id", "slotId"),
    }


def _plan_unload(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_unload_drive(
        barcode=arguments["barcode"], drive=arguments["drive"], slot=arguments["slot"]
    )
    return result


def _describe_unload(_: dict[str, Any], plan: JSONDict) -> str:
    drive = f"drive {plan['driveId']}"
    if plan.get("driveSerial"):
        drive += f" (serial {plan['driveSerial']})"
    chosen = " — the lowest free slot" if plan.get("slotChosenAutomatically") else ""
    return (
        f"Unload {plan['barcode']} from {drive} into slot {plan['slotId']}{chosen}.\n"
        f"  {sentence_case(drive)} becomes free. Nothing on the cartridge changes; LTFS "
        "is not mounted on it."
    )


def _apply_unload(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    result: JSONDict = facade.unload_drive(
        barcode=action.arguments["barcode"],
        drive=action.arguments["drive"],
        slot=action.arguments["slot"],
    )
    return result


# -- move -------------------------------------------------------------------


def _normalize_move(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "barcode": _barcode(arguments, "barcode", "tape"),
        "slot": _number(arguments, "slot", "slot_id", "slotId", "dest_slot", "destination"),
    }


def _plan_move(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_move_tape(
        barcode=arguments["barcode"], slot=arguments["slot"]
    )
    return result


def _describe_move(_: dict[str, Any], plan: JSONDict) -> str:
    return (
        f"Move {plan['barcode']} from slot {plan['sourceSlotId']} to slot "
        f"{plan['destinationSlotId']}.\n"
        "  Both are data storage slots inside the library; nothing is exported and "
        "nothing is written."
    )


def _apply_move(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    result: JSONDict = facade.move_tape(
        barcode=action.arguments["barcode"], slot=action.arguments["slot"]
    )
    return result


# -- format -----------------------------------------------------------------


def _normalize_format(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"barcode": _barcode(arguments, "barcode", "tape")}


def _plan_format(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    """Phase one of the two-phase flow: the real dry run, minting a real token."""
    result: JSONDict = facade.plan_format_tape(barcode=arguments["barcode"])
    return result


def _describe_format(_: dict[str, Any], plan: JSONDict) -> str:
    barcode = plan["barcode"]
    files = int(plan.get("archivedFileCount") or 0)
    group = plan.get("volumeGroup")
    lost = (
        f"{files} archived file(s)"
        if files
        else "no catalogued files (the catalog has no record of anything on it)"
    )
    where = f" in volume group {group!r}" if group else ""
    lines = [
        f"FORMAT {barcode}. This is irreversible and there is no undo.",
        f"  Everything on the cartridge is destroyed: {lost}{where}, "
        f"{human_bytes(plan.get('usedBytes'))} recorded as used of "
        f"{human_bytes(plan.get('capacityBytes'))} capacity.",
        "  WORM: not reported by this backend — check the cartridge label yourself if "
        "it matters.",
        f"  The format writes {plan.get('wouldWrite')}.",
        f"  A one-time safety token was issued by the dry run and expires in "
        f"{plan.get('tokenTtlSeconds')}s — if it expires while you check the "
        "cartridge, ask again and a fresh dry run runs.",
    ]
    if plan.get("willBeLoaded"):
        lines.append(
            f"  The cartridge is in {plan.get('currentlyIn')} and will be loaded into "
            f"drive {plan.get('driveId')} to format it, then returned."
        )
    else:
        lines.append(f"  The cartridge is already in {plan.get('currentlyIn')}.")
    samples = list(plan.get("samplePaths") or [])
    if samples:
        lines.append(f"  Files that would be lost include: {', '.join(samples)}")
    for warning in plan.get("warnings") or []:
        lines.append(f"  Dry run: {warning}")
    lines.append(
        f"  Type the barcode {barcode} to confirm. Anything else — including \"y\" — cancels."
    )
    return "\n".join(lines)


def _grade_format(_: dict[str, Any], plan: JSONDict) -> tuple[ConfirmationGrade, str | None]:
    return ConfirmationGrade.TYPED, str(plan["barcode"])


def _apply_format(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    """Phase two: hand the dry run's token back to the confirm path.

    ``action.token`` is what the plan minted. There is no branch here that formats
    without it — the facade refuses a missing token, and ``FormatService.confirm``
    refuses one that is not in ``safety_tokens``.
    """
    result: JSONDict = facade.format_tape(
        barcode=action.arguments["barcode"], token=action.token
    )
    return result


# -- archive ----------------------------------------------------------------


def _normalize_archive(arguments: Mapping[str, Any]) -> dict[str, Any]:
    path = _optional(arguments, "path", "source_path", "sourcePath", "source")
    group = _optional(arguments, "volume_group", "volumeGroup", "name", "pool")
    return {
        "path": str(clean_path(path, "source path")),
        "volume_group": None if group is None else str(group).strip(),
    }


def _plan_archive(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_archive_path(
        path=arguments["path"], volume_group=arguments["volume_group"]
    )
    return result


def _describe_archive(_: dict[str, Any], plan: JSONDict) -> str:
    samples = ", ".join(plan.get("sampleFiles") or [])
    more = " …" if int(plan.get("fileCount") or 0) > len(plan.get("sampleFiles") or []) else ""
    return (
        f"Archive {plan['sourcePath']} into volume group {plan['volumeGroup']!r}: "
        f"{plan['fileCount']} file(s), {human_bytes(plan.get('byteCount'))}.\n"
        f"  Written to the {plan['tapeCount']} tape(s) in that pool; existing data on "
        "them is not touched, and the source files are left where they are.\n"
        f"  Including: {samples}{more}\n"
        "  This runs synchronously and can take minutes — the prompt will not come "
        "back until it finishes."
    )


def _apply_archive(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    result: JSONDict = facade.archive_path(
        path=action.arguments["path"], volume_group=action.arguments["volume_group"]
    )
    return result


# -- restore ----------------------------------------------------------------


def _normalize_restore(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": clean_catalog_path(
            _optional(arguments, "path", "catalog_path", "catalogPath", "source")
        ),
        "dest": str(
            clean_path(
                _optional(arguments, "dest", "dest_path", "destPath", "destination", "to"),
                "destination path",
            )
        ),
    }


def _plan_restore(facade: MediaFacade, arguments: dict[str, Any]) -> JSONDict:
    result: JSONDict = facade.plan_restore_path(
        path=arguments["path"], dest=arguments["dest"]
    )
    return result


def _describe_restore(_: dict[str, Any], plan: JSONDict) -> str:
    tapes = ", ".join(plan.get("tapes") or [])
    lines = [
        f"Restore {plan['catalogPath']} ({human_bytes(plan.get('sizeBytes'))}, from tape "
        f"{tapes}) to {plan['destinationPath']}.",
    ]
    if plan.get("overwrites"):
        lines.append(
            f"  THAT FILE ALREADY EXISTS ({human_bytes(plan.get('existingBytes'))}) and "
            "will be OVERWRITTEN. Its current contents are lost and there is no undo."
        )
        lines.append(
            f'  Type {OVERWRITE_WORD} to confirm. Anything else — including "y" — cancels.'
        )
    else:
        lines.append(
            "  Nothing is overwritten; the destination does not exist yet. The tape is "
            "mounted read-only."
        )
    lines.append(
        "  This runs synchronously and can take minutes — the prompt will not come back "
        "until it finishes."
    )
    return "\n".join(lines)


def _grade_restore(_: dict[str, Any], plan: JSONDict) -> tuple[ConfirmationGrade, str | None]:
    """Destructive only when it would overwrite — and that is read from the plan.

    The grade therefore cannot be chosen by the model: it is a fact about the
    filesystem at plan time, re-checked inside the write path by the facade.
    """
    if plan.get("overwrites"):
        return ConfirmationGrade.TYPED, OVERWRITE_WORD
    return ConfirmationGrade.YES_NO, None


def _apply_restore(facade: MediaFacade, action: PendingMediaAction) -> JSONDict:
    """Carries the plan's overwrite verdict into the write path.

    That verdict is what chose the confirmation grade, so the facade re-checks it
    against the filesystem and refuses if it changed. Without it, a ``y`` given
    for "nothing is overwritten" destroys a file that appeared in between.
    """
    result: JSONDict = facade.restore_path(
        path=action.arguments["path"],
        dest=action.arguments["dest"],
        expect_overwrite=bool(action.plan.get("overwrites")),
    )
    return result


def _tool_definitions() -> list[MediaTool]:
    barcode_parameter = {
        "type": "string",
        "description": "Barcode of the cartridge, e.g. OB0003L8.",
    }
    return [
        MediaTool(
            name="load_tape",
            description=(
                "Load a cartridge from its slot into a tape drive. The operator is "
                "shown exactly which tape, slot and drive and must confirm before it "
                "runs. Nothing is written to the tape."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "barcode": barcode_parameter,
                    "drive": {
                        "type": "integer",
                        "description": "Drive to load into. Omit to use a free drive.",
                    },
                },
                "required": ["barcode"],
            },
            normalize=_normalize_load,
            plan=_plan_load,
            describe=_describe_load,
            grade=_yes_no,
            apply=_apply_load,
        ),
        MediaTool(
            name="unload_drive",
            description=(
                "Return a loaded cartridge from a drive to a storage slot. Give the "
                "barcode or the drive number. Refused while LTFS is mounted on that "
                "drive. The operator confirms before it runs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "barcode": barcode_parameter,
                    "drive": {"type": "integer", "description": "Drive to unload."},
                    "slot": {
                        "type": "integer",
                        "description": "Slot to put it in. Omit to use a free slot.",
                    },
                },
            },
            normalize=_normalize_unload,
            plan=_plan_unload,
            describe=_describe_unload,
            grade=_yes_no,
            apply=_apply_unload,
        ),
        MediaTool(
            name="move_tape",
            description=(
                "Move a cartridge from one storage slot to another inside the library. "
                "Import/export elements are not valid destinations. The operator "
                "confirms before it runs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "barcode": barcode_parameter,
                    "slot": {"type": "integer", "description": "Destination storage slot."},
                },
                "required": ["barcode", "slot"],
            },
            normalize=_normalize_move,
            plan=_plan_move,
            describe=_describe_move,
            grade=_yes_no,
            apply=_apply_move,
        ),
        MediaTool(
            name="format_tape",
            description=(
                "Format (erase) a cartridge with LTFS. IRREVERSIBLE: everything on it "
                "is destroyed. Runs the safety dry run first, then requires the "
                "operator to type the barcode; a yes is not accepted."
            ),
            parameters={
                "type": "object",
                "properties": {"barcode": barcode_parameter},
                "required": ["barcode"],
            },
            normalize=_normalize_format,
            plan=_plan_format,
            describe=_describe_format,
            grade=_grade_format,
            apply=_apply_format,
            long_running=True,
        ),
        MediaTool(
            name="archive_path",
            description=(
                "Copy a local file or directory onto the tapes of a volume group and "
                "record it in the catalog. Additive: nothing already on tape is "
                "touched. The operator confirms before it runs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path of the file or directory to archive.",
                    },
                    "volume_group": {
                        "type": "string",
                        "description": "Existing volume group to archive into.",
                    },
                },
                "required": ["path", "volume_group"],
            },
            normalize=_normalize_archive,
            plan=_plan_archive,
            describe=_describe_archive,
            grade=_yes_no,
            apply=_apply_archive,
            long_running=True,
        ),
        MediaTool(
            name="restore_path",
            description=(
                "Restore an archived file from tape to a local path, verifying its "
                "checksum. If the destination file already exists the operator must "
                "type a word to confirm the overwrite; a yes is not accepted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Catalog path of the archived file, e.g. /photos/a.raw",
                    },
                    "dest": {
                        "type": "string",
                        "description": "Absolute local destination path or directory.",
                    },
                },
                "required": ["path", "dest"],
            },
            normalize=_normalize_restore,
            plan=_plan_restore,
            describe=_describe_restore,
            grade=_grade_restore,
            apply=_apply_restore,
            long_running=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MediaToolRegistry:
    """Name -> :class:`MediaTool`, validated against the tier-2 allowlist.

    The allowlist is fail-closed and the other two registries are exclusions: a
    name that belongs to tier 1 or to the read-only tools cannot be registered
    here, so dispatch can never be ambiguous and a tier boundary cannot be crossed
    by renaming a tool.
    """

    def __init__(self, tools: list[MediaTool]) -> None:
        by_name: dict[str, MediaTool] = {}
        for tool in tools:
            if tool.name not in MEDIA_TOOL_NAMES:
                raise MediaRegistryViolationError(
                    f"Tool {tool.name!r} is not on the assistant media allowlist. Tier-2 "
                    "tools move media or write tape data; add the name to "
                    "MEDIA_TOOL_NAMES only after confirming the operation goes through "
                    "an existing service and has a confirmation grade matching its "
                    "consequence."
                )
            if tool.name in SETUP_TOOL_NAMES:
                raise MediaRegistryViolationError(
                    f"Tool {tool.name!r} is already a tier-1 setup tool. The two tiers "
                    "have different confirmation strengths and must not share a name."
                )
            if tool.name in READ_ONLY_TOOL_NAMES:
                raise MediaRegistryViolationError(
                    f"Tool {tool.name!r} is already a read-only tool; a media tool must "
                    "not shadow a read."
                )
            if tool.name in by_name:
                raise MediaRegistryViolationError(f"Duplicate media tool {tool.name!r}")
            by_name[tool.name] = tool
        self._tools = by_name

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def get(self, name: str) -> MediaTool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"Unknown media tool {name!r}") from None

    def action_key(self, name: str, arguments: Mapping[str, Any]) -> str:
        """The decline-cache identity of a call, WITHOUT planning it.

        Planning a format runs the dry run, which mints and persists a live safety
        token. Asking "has the operator already refused this?" must therefore be
        answerable from the normalized arguments alone, or every repeat proposal
        of a refused format leaves another live authorization in the database.
        """
        tool = self.get(name)
        normalized = tool.normalize(arguments)
        return PendingMediaAction(
            tool=name, arguments=normalized, preview="", grade=ConfirmationGrade.YES_NO
        ).key

    def plan(
        self, name: str, facade: MediaFacade, arguments: Mapping[str, Any]
    ) -> PendingMediaAction:
        """Validate against the live library and build the confirmation preview.

        Raises :class:`~openblade.assistant.errors.MediaRefusedError` if the target
        is not unambiguous, so the operator is never prompted about an action that
        would then have to guess. For ``format_tape`` this is also where the dry run
        runs and the one-time safety token is minted.
        """
        tool = self.get(name)
        normalized = tool.normalize(arguments)
        plan = tool.plan(facade, normalized)
        grade, required = tool.grade(normalized, plan)
        action = PendingMediaAction(
            tool=name,
            arguments=normalized,
            preview=tool.describe(normalized, plan),
            grade=grade,
            plan=plan,
            required_response=required,
            token=plan.get("token"),
        )
        log_action(action, outcome="proposed", detail={"grade": grade.value})
        return action

    def authorize(
        self, action: PendingMediaAction, response: str | None
    ) -> MediaAuthorization | None:
        """Turn what the operator typed into an authorization, or ``None``."""
        if not verify_response(action, response):
            return None
        return MediaAuthorization(
            action_key=action.key, grade=action.grade, response=(response or "").strip()
        )

    def perform(
        self,
        action: PendingMediaAction,
        facade: MediaFacade,
        authorization: MediaAuthorization | None,
    ) -> JSONDict:
        """Run a confirmed action, re-verifying the authorization first.

        This is not a formality and it is not the same check ``authorize`` made: it
        is what makes "a tier-2 tool cannot execute without its strong confirm" a
        property of this module rather than of the REPL. Delete any of the three
        checks below and a named test in ``tests/safety`` fails.
        """
        if authorization is None:
            raise MediaNotAuthorizedError(
                f"{action.tool} reached the write path with no operator authorization."
            )
        if authorization.action_key != action.key:
            raise MediaNotAuthorizedError(
                f"The authorization presented for {action.tool} was issued for a "
                "different action."
            )
        if authorization.grade is not action.grade:
            raise MediaNotAuthorizedError(
                f"{action.tool} requires a {action.grade.value} confirmation; the "
                f"authorization carries {authorization.grade.value}."
            )
        if not verify_response(action, authorization.response):
            raise MediaNotAuthorizedError(
                f"The confirmation for {action.tool} does not satisfy its "
                f"{action.grade.value} grade."
            )
        tool = self.get(action.tool)
        result = tool.apply(facade, action)
        log_action(action, outcome="executed", detail=result)
        return result

    def schemas(self) -> list[JSONDict]:
        return [self._tools[name].schema() for name in sorted(self._tools)]


def build_media_registry(extra: list[MediaTool] | None = None) -> MediaToolRegistry:
    """Build the tier-2 registry.

    ``extra`` exists so tests can prove the allowlist guards fire; production code
    passes nothing.
    """
    return MediaToolRegistry([*_tool_definitions(), *(extra or [])])
