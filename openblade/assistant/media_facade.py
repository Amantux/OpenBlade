"""The tier-2 media facade — the assistant's only route to robotics and tape data.

Tier 1 (:mod:`openblade.assistant.setup_facade`) is catalog-only and reversible by
hand. This module is the other thing the assistant may execute: the six media and
robotics operations the operator has to confirm *strongly* first.

* ``load_tape`` / ``unload_drive`` / ``move_tape`` — robotics. Nothing is written,
  nothing is lost; they move a cartridge between a slot and a drive.
* ``archive_path`` — copies a local directory or file onto tape. Additive.
* ``restore_path`` — copies an archived file back to local disk. Additive *unless*
  the destination file already exists, in which case it destroys it.
* ``format_tape`` — irreversible. Everything on the cartridge is lost.

Two things make this a new *caller* rather than a new *path*:

1. Every operation goes through the service the REST API and the CLI already use —
   :func:`openblade.nas.tape_orchestrator.execute_tape_request` for robotics,
   ``FormatService.dry_run``/``confirm`` for format, ``ArchiveService.enqueue`` and
   ``RestoreService.enqueue`` for bulk data. The job queue's drive and changer
   ownership, ``RealHardwareGuard``, the mount-state gates and the one-time
   ``SafetyToken`` all still apply, because they live *inside* those services and
   this module does not reimplement any of them. No service capability is invented
   here: there is no eject, no import/export, no delete, because the assistant has
   no business owning one and the orchestrator's own export refusal stands.
2. :class:`MediaFacade` is an :class:`~openblade.assistant.readonly.AllowlistProxy`
   with a custom resolver, exactly like the tier-1 facade. It holds no instance
   state, an allowlisted name resolves to an operation *defined in this module*,
   and every callable it hands out is a
   :class:`~openblade.assistant.readonly.SealedCall`. ``facade._target``,
   ``facade.__init__``, ``facade.library`` and ``operation.__closure__`` all raise.

Reads go through the tier-1 read proxy. The facade validates against a
:class:`~openblade.assistant.readonly.ReadOnlyProxy` over the catalog and over
``InventoryService``, so its *validation* half cannot write even by accident, and
the live backends are reachable only as opaque arguments handed to the services
above. That is also why this module never calls a load/unload/move/inventory method
on a library backend or a format/mount method on an LTFS backend directly:
SAFETY_003 (:mod:`openblade.safety.import_guard`) forbids that outside the
authorized access points, and the assistant is deliberately not one of them. (The
forbidden call patterns are described here rather than written out, because the
guard is a line-based substring scan and spelling them would trip it — the same
reason :mod:`openblade.assistant.readonly` describes its one indirectly.)

Ambiguity refuses even when confirmed. Every operation has a ``plan_*`` twin that
resolves the target against the live library before the operator is asked, and the
executing operation resolves it *again* inside the write path. An unknown barcode,
a barcode that somehow appears in two places, an occupied target drive or slot, or
a drive with LTFS still mounted stops the action with
:class:`~openblade.assistant.errors.MediaRefusedError` and the candidates the
operator plausibly meant — before any prompt is printed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from openblade.assistant.errors import (
    MediaFacadeViolationError,
    MediaOperationFailedError,
    MediaRefusedError,
)
from openblade.assistant.readonly import AllowlistProxy, ReadOnlyProxy
from openblade.domain.errors import OpenBladeError, safe_job_error
from openblade.nas.tape_orchestrator import TapeOperationFailedError, execute_tape_request
from openblade.nas.types import TapeOpRequest, TapeOpType

JSONDict = dict[str, Any]

# Mount states in which a drive is holding a volume. "Never unload while LTFS is
# mounted or dirty" is a project non-negotiable; the orchestrator does not check it
# (it unloads what it is told to), so the assistant refuses before it asks.
BUSY_MOUNT_STATES: frozenset[str] = frozenset({"mounted_ro", "mounted_rw", "dirty"})

# Most files one confirmed archive may take. An operator talking to a chat
# assistant is archiving a directory they can describe in a sentence; a request
# carrying 40k files is a misunderstanding, and it is also a preview nobody can
# meaningfully read before saying yes. ``openblade archive`` has no such cap.
MAX_ARCHIVE_FILES = 500
_MAX_CANDIDATES = 5

# The word a restore must be confirmed with when it would overwrite a file.
OVERWRITE_WORD = "OVERWRITE"


@dataclass(frozen=True)
class MediaBundle:
    """Everything the tier-2 operations act on, in one reviewable object.

    ``catalog`` and ``inventory`` are read proxies: the validation half of this
    module cannot write through them. ``library``, ``ltfs`` and ``catalog_repo``
    are live and are never *called* here — they are handed straight to
    ``execute_tape_request``, which is the authorized hardware access point.
    """

    catalog: ReadOnlyProxy
    inventory: ReadOnlyProxy
    catalog_repo: Any
    library: Any
    ltfs: Any
    format_service: Any
    archive_service: Any
    restore_service: Any
    #: ``OPENBLADE_DRIVE_SERIAL_MAP`` as parsed config: (serial, drive element).
    #: A preview that says "drive 1" is not checkable by a human standing at the
    #: rack; one that says "drive 1 (serial OBLADE_D02)" is.
    drive_serials: tuple[tuple[str, int], ...] = ()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def human_bytes(value: int | None) -> str:
    """Bytes as a short decimal string. Previews are read by humans under pressure."""
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(size) < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000.0
    return f"{size:.1f} TB"  # pragma: no cover - unreachable, loop returns at TB


def clean_barcode(raw: object) -> str:
    """One barcode, normalized. Refuses anything that is not one."""
    barcode = "" if raw is None else str(raw).strip().upper()
    if not barcode:
        raise MediaRefusedError("A tape barcode is required.", code="missing_barcode")
    if not barcode.isalnum() or len(barcode) > 32:
        raise MediaRefusedError(
            f"{barcode!r} is not a barcode. Barcodes are alphanumeric, e.g. OB0003L8.",
            code="invalid_barcode",
        )
    return barcode


def _clean_int(raw: object, what: str) -> int | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise MediaRefusedError(
            f"{raw!r} is not a {what} number.", code=f"invalid_{what.replace(' ', '_')}"
        ) from None


def clean_path(raw: object, what: str) -> Path:
    """A local filesystem path, absolute and normalized.

    Relative paths are refused rather than resolved against the process's working
    directory: the assistant's cwd is wherever the operator happened to start the
    REPL, and "archive ./photos" meaning something different per session is how a
    confirmed action ends up writing the wrong tree to tape.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise MediaRefusedError(f"A {what} is required.", code=f"missing_{what}")
    if not text.isprintable():
        raise MediaRefusedError(
            f"That {what} contains control characters.", code=f"invalid_{what}"
        )
    path = Path(text)
    if not path.is_absolute():
        raise MediaRefusedError(
            f"The {what} must be absolute (start with /); {text!r} is relative.",
            code=f"relative_{what}",
        )
    # ``resolve`` and not ``absolute``: '..' segments are collapsed here so the
    # preview names the path that will actually be touched.
    return Path(path.resolve())


def clean_catalog_path(raw: object) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise MediaRefusedError("A catalog path is required.", code="missing_catalog_path")
    if not text.startswith("/"):
        raise MediaRefusedError(
            f"A catalog path starts with /; {text!r} does not.", code="invalid_catalog_path"
        )
    return str(PurePosixPath(text))


# ---------------------------------------------------------------------------
# Live-library resolution. Everything here reads; nothing here acts.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TapeLocation:
    """Where one cartridge physically is right now."""

    barcode: str
    slot_id: int | None
    drive_id: int | None

    @property
    def in_drive(self) -> bool:
        return self.drive_id is not None


def _snapshot(bundle: MediaBundle) -> Any:
    return bundle.inventory.snapshot()


def _known_barcodes(inventory: Any) -> list[str]:
    found = [str(slot.barcode) for slot in inventory.slots if slot.barcode is not None]
    found.extend(str(drive.barcode) for drive in inventory.drives if drive.barcode is not None)
    return sorted(set(found))


def _barcode_candidates(inventory: Any, wanted: str) -> tuple[str, ...]:
    """Barcodes the operator plausibly meant, ranked by kind of evidence.

    Same shape as the tier-1 ranking: a shared prefix beats a substring match beats
    "any tape in the library". Deliberately not a similarity score — a score puts a
    single number between the operator and an irreversible operation.
    """
    known = _known_barcodes(inventory)
    stem = wanted[:4]
    prefix = [code for code in known if stem and code.startswith(stem)]
    contains = [code for code in known if stem and stem in code and code not in prefix]
    ordered: list[str] = []
    for code in [*prefix, *contains, *known]:
        if code not in ordered:
            ordered.append(code)
    return tuple(ordered[:_MAX_CANDIDATES])


def _locate(bundle: MediaBundle, barcode: str) -> tuple[Any, TapeLocation]:
    """Resolve a barcode to exactly one place in the library, or refuse."""
    inventory = _snapshot(bundle)
    slots = [slot for slot in inventory.slots if str(slot.barcode) == barcode]
    drives = [drive for drive in inventory.drives if str(drive.barcode) == barcode]
    if not slots and not drives:
        raise MediaRefusedError(
            f"No tape with barcode {barcode} is present in this library.",
            code="unknown_barcode",
            candidates=_barcode_candidates(inventory, barcode),
        )
    if len(slots) + len(drives) > 1:
        # Physically impossible in a healthy library, which is exactly why it must
        # refuse rather than pick: it means the inventory is stale or the changer
        # is lying, and acting on either is how media gets crushed.
        places = [f"slot {slot.slot_id}" for slot in slots]
        places.extend(f"drive {drive.drive_id}" for drive in drives)
        raise MediaRefusedError(
            f"Barcode {barcode} appears in {len(places)} places at once "
            f"({', '.join(places)}). The inventory disagrees with itself; re-run "
            "an inventory before moving it.",
            code="barcode_in_two_places",
            candidates=tuple(places),
        )
    if drives:
        return inventory, TapeLocation(barcode, None, int(drives[0].drive_id))
    return inventory, TapeLocation(barcode, int(slots[0].slot_id), None)


def _drive_serial(bundle: MediaBundle, drive_id: int) -> str | None:
    for serial, element in bundle.drive_serials:
        if element == drive_id:
            return serial
    return None


def _drive_label(bundle: MediaBundle, drive_id: int) -> str:
    serial = _drive_serial(bundle, drive_id)
    return f"drive {drive_id} (serial {serial})" if serial else f"drive {drive_id}"


def sentence_case(text: str) -> str:
    """Upper-case the first character and leave the rest alone.

    ``str.capitalize`` lower-cases everything after it, which turned
    "drive 1 (serial OBLADE_D02)" into "...(serial oblade_d02)" — a serial an
    operator is meant to check against a label on the rack, silently mangled.
    """
    return text[:1].upper() + text[1:]


def _drive(inventory: Any, drive_id: int) -> Any:
    for drive in inventory.drives:
        if int(drive.drive_id) == drive_id:
            return drive
    raise MediaRefusedError(
        f"There is no drive {drive_id} in this library.",
        code="unknown_drive",
        candidates=tuple(f"drive {drive.drive_id}" for drive in inventory.drives),
    )


def _slot(inventory: Any, slot_id: int) -> Any:
    for slot in inventory.slots:
        if int(slot.slot_id) == slot_id:
            return slot
    raise MediaRefusedError(
        f"Slot {slot_id} is not a data storage slot in this library "
        f"(valid: {min((int(s.slot_id) for s in inventory.slots), default=0)}-"
        f"{max((int(s.slot_id) for s in inventory.slots), default=0)}). Moving media "
        "to an import/export element is not something the assistant does.",
        code="unknown_slot",
    )


def _mount_state(drive: Any) -> str:
    state = getattr(drive, "mount_state", None)
    return str(getattr(state, "value", state) or "unmounted")


def _require_free_drive(bundle: MediaBundle, inventory: Any, drive_id: int) -> Any:
    drive = _drive(inventory, drive_id)
    if drive.barcode is not None:
        raise MediaRefusedError(
            f"{sentence_case(_drive_label(bundle, drive_id))} already holds "
            f"{drive.barcode}. Unload it first, or name a different drive.",
            code="drive_occupied",
            candidates=tuple(
                f"drive {other.drive_id}" for other in inventory.drives if other.barcode is None
            ),
        )
    return drive


def _pick_free_drive(bundle: MediaBundle, inventory: Any) -> int:
    free = [int(drive.drive_id) for drive in inventory.drives if drive.barcode is None]
    if not free:
        raise MediaRefusedError(
            "Every drive is occupied; there is nowhere to load this tape.",
            code="no_free_drive",
            candidates=tuple(
                f"drive {drive.drive_id} holds {drive.barcode}" for drive in inventory.drives
            ),
        )
    return min(free)


def _pick_free_slot(inventory: Any) -> int:
    free = [int(slot.slot_id) for slot in inventory.slots if slot.barcode is None]
    if not free:
        raise MediaRefusedError(
            "Every storage slot is full; there is nowhere to put this cartridge.",
            code="no_free_slot",
        )
    return min(free)


def _require_free_slot(inventory: Any, slot_id: int) -> Any:
    slot = _slot(inventory, slot_id)
    if slot.barcode is not None:
        raise MediaRefusedError(
            f"Slot {slot_id} already holds {slot.barcode}.",
            code="slot_occupied",
            candidates=tuple(
                f"slot {other.slot_id}" for other in inventory.slots if other.barcode is None
            )[:_MAX_CANDIDATES],
        )
    return slot


def _require_unmounted(bundle: MediaBundle, drive: Any) -> None:
    state = _mount_state(drive)
    if state in BUSY_MOUNT_STATES:
        raise MediaRefusedError(
            f"{sentence_case(_drive_label(bundle, int(drive.drive_id)))} is {state}: LTFS "
            "still holds the volume. Unmounting someone else's volume is not a "
            "decision the assistant makes — unmount it first.",
            code="drive_mounted",
        )


# ---------------------------------------------------------------------------
# Robotics: load / unload / move
# ---------------------------------------------------------------------------

def _run_tape_op(bundle: MediaBundle, request: TapeOpRequest) -> Any:
    """One robotics operation through the authorized choke point, curated on failure.

    ``execute_tape_request`` is the same call the CLI and ``POST /tape-ops/execute``
    make, so the drive and barcode locks, the audit record and the export refusal
    all still apply. ``raise_on_failed`` turns a recorded failure into an exception
    whose message is the orchestrator's own per-op-type constant — never raw
    ``mtx`` stderr — which is then re-raised typed so the session can report it
    without ever touching upstream text.
    """
    try:
        return execute_tape_request(
            bundle.catalog_repo, bundle.library, bundle.ltfs, request, raise_on_failed=True
        )
    except TapeOperationFailedError as exc:
        raise MediaOperationFailedError(str(exc)) from None
    except MediaRefusedError:
        raise
    except OpenBladeError as exc:
        # Typed OpenBlade errors carry operator-written messages and are safe.
        raise MediaOperationFailedError(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - curated, never echoed raw
        raise MediaOperationFailedError(safe_job_error(exc)) from None


def _run_service(what: str, call: Callable[[], Any]) -> Any:
    """Run a job service, curating anything it raises.

    ``safe_job_error`` is the project's existing rule for this boundary: a typed
    OpenBlade error's message passes through, everything else is reduced to a class
    name because ``CommandError`` carries argv and raw ``mkltfs``/``mtx`` stderr.
    """
    try:
        return call()
    except MediaRefusedError:
        raise
    except Exception as exc:  # noqa: BLE001 - curated, never echoed raw
        raise MediaOperationFailedError(f"{what} failed: {safe_job_error(exc)}") from None



def _resolve_load(bundle: MediaBundle, *, barcode: object, drive: object) -> JSONDict:
    code = clean_barcode(barcode)
    wanted_drive = _clean_int(drive, "drive")
    inventory, location = _locate(bundle, code)
    if location.in_drive:
        raise MediaRefusedError(
            f"Tape {code} is already loaded in "
            f"{_drive_label(bundle, int(location.drive_id or 0))}.",
            code="already_loaded",
        )
    if wanted_drive is None:
        drive_id = _pick_free_drive(bundle, inventory)
    else:
        drive_id = wanted_drive
        _require_free_drive(bundle, inventory, drive_id)
    _require_unmounted(bundle, _drive(inventory, drive_id))
    return {
        "barcode": code,
        "slotId": location.slot_id,
        "driveId": drive_id,
        "driveSerial": _drive_serial(bundle, drive_id),
        "driveChosenAutomatically": wanted_drive is None,
    }


def _plan_load_tape(bundle: MediaBundle, *, barcode: object, drive: object = None) -> JSONDict:
    return _resolve_load(bundle, barcode=barcode, drive=drive)


def _load_tape(bundle: MediaBundle, *, barcode: object, drive: object = None) -> JSONDict:
    # Re-resolved inside the write path: the plan ran before the operator answered,
    # and the library may have moved underneath us.
    resolved = _resolve_load(bundle, barcode=barcode, drive=drive)
    record = _run_tape_op(
        bundle,
        TapeOpRequest(
            op_type=TapeOpType.LOAD,
            barcode=resolved["barcode"],
            drive_id=resolved["driveId"],
            slot_id=resolved["slotId"],
            requested_by="assistant",
        ),
    )
    return {**resolved, "opId": record.op_id, "message": record.result.get("message", "Loaded")}


def _loaded_drive_candidates(inventory: Any) -> tuple[str, ...]:
    return tuple(
        f"{drive.barcode} in drive {drive.drive_id}"
        for drive in inventory.drives
        if drive.barcode is not None
    )


def _barcode_in_drive(bundle: MediaBundle, drive_id: int) -> str:
    """The tape in ``drive_id``, or a refusal naming what is actually loaded."""
    inventory = _snapshot(bundle)
    drive = _drive(inventory, drive_id)
    if drive.barcode is None:
        raise MediaRefusedError(
            f"{sentence_case(_drive_label(bundle, drive_id))} is empty; there is nothing "
            "to unload.",
            code="drive_empty",
            candidates=_loaded_drive_candidates(inventory),
        )
    return str(drive.barcode)


def _resolve_unload(
    bundle: MediaBundle, *, barcode: object = None, drive: object = None, slot: object = None
) -> JSONDict:
    wanted_slot = _clean_int(slot, "slot")
    named_drive = _clean_int(drive, "drive")
    given = "" if barcode is None else str(barcode).strip()
    if not given and named_drive is None:
        # Not "guess the only loaded drive": with two drives loaded that guess is a
        # coin toss on which tape leaves the drive, and the operator would be
        # confirming a sentence that names the wrong one.
        raise MediaRefusedError(
            "Which tape should be unloaded? Name the barcode or the drive.",
            code="unload_target_unspecified",
            candidates=_loaded_drive_candidates(_snapshot(bundle)),
        )
    code = clean_barcode(given) if given else _barcode_in_drive(bundle, int(named_drive or 0))
    inventory, location = _locate(bundle, code)
    if not location.in_drive:
        raise MediaRefusedError(
            f"Tape {code} is not in a drive; it is in slot {location.slot_id}.",
            code="not_loaded",
            candidates=_loaded_drive_candidates(inventory),
        )
    drive_id = int(location.drive_id or 0)
    if named_drive is not None and named_drive != drive_id:
        raise MediaRefusedError(
            f"Tape {code} is in drive {drive_id}, not drive {named_drive}. Nothing was "
            "moved; say which of the two you meant.",
            code="drive_barcode_mismatch",
            candidates=_loaded_drive_candidates(inventory),
        )
    _require_unmounted(bundle, _drive(inventory, drive_id))
    if wanted_slot is None:
        slot_id = _pick_free_slot(inventory)
    else:
        slot_id = wanted_slot
        _require_free_slot(inventory, slot_id)
    return {
        "barcode": code,
        "driveId": drive_id,
        "driveSerial": _drive_serial(bundle, drive_id),
        "slotId": slot_id,
        "slotChosenAutomatically": wanted_slot is None,
    }


def _plan_unload_drive(
    bundle: MediaBundle, *, barcode: object = None, drive: object = None, slot: object = None
) -> JSONDict:
    return _resolve_unload(bundle, barcode=barcode, drive=drive, slot=slot)


def _unload_drive(
    bundle: MediaBundle, *, barcode: object = None, drive: object = None, slot: object = None
) -> JSONDict:
    resolved = _resolve_unload(bundle, barcode=barcode, drive=drive, slot=slot)
    record = _run_tape_op(
        bundle,
        TapeOpRequest(
            op_type=TapeOpType.UNLOAD,
            barcode=resolved["barcode"],
            drive_id=resolved["driveId"],
            slot_id=resolved["slotId"],
            requested_by="assistant",
        ),
    )
    return {**resolved, "opId": record.op_id, "message": record.result.get("message", "Unloaded")}


def _resolve_move(bundle: MediaBundle, *, barcode: object, slot: object) -> JSONDict:
    code = clean_barcode(barcode)
    destination = _clean_int(slot, "slot")
    if destination is None:
        raise MediaRefusedError(
            "A destination slot is required to move a tape.", code="missing_slot"
        )
    inventory, location = _locate(bundle, code)
    if location.in_drive:
        raise MediaRefusedError(
            f"Tape {code} is in {_drive_label(bundle, int(location.drive_id or 0))}, not a "
            "slot. Unload it instead; slot-to-slot move does not apply to a loaded tape.",
            code="loaded_not_in_slot",
        )
    source = int(location.slot_id or 0)
    if destination == source:
        raise MediaRefusedError(
            f"Tape {code} is already in slot {source}.", code="same_slot"
        )
    _require_free_slot(inventory, destination)
    return {"barcode": code, "sourceSlotId": source, "destinationSlotId": destination}


def _plan_move_tape(bundle: MediaBundle, *, barcode: object, slot: object = None) -> JSONDict:
    return _resolve_move(bundle, barcode=barcode, slot=slot)


def _move_tape(bundle: MediaBundle, *, barcode: object, slot: object = None) -> JSONDict:
    resolved = _resolve_move(bundle, barcode=barcode, slot=slot)
    record = _run_tape_op(
        bundle,
        TapeOpRequest(
            op_type=TapeOpType.MOVE,
            barcode=resolved["barcode"],
            slot_id=resolved["sourceSlotId"],
            requested_by="assistant",
            # The orchestrator refuses a destination that is not a data storage
            # slot — that is the guard that stopped a cartridge being ejected into
            # the mailslot. We validate the same thing first so the operator is
            # never asked about a move that cannot happen.
            extras={
                "source_slot_id": resolved["sourceSlotId"],
                "dest_slot_id": resolved["destinationSlotId"],
            },
        ),
    )
    return {**resolved, "opId": record.op_id, "message": record.result.get("message", "Moved")}


# ---------------------------------------------------------------------------
# Format: the two-phase destructive flow, used and never bypassed
# ---------------------------------------------------------------------------


def _format_consequences(bundle: MediaBundle, barcode: str) -> JSONDict:
    """What is lost if this cartridge is formatted, read from the catalog."""
    cartridge = bundle.catalog.get_cartridge(barcode)
    instances = bundle.catalog.list_instances_for_barcode(barcode)
    groups = {group.id: group.name for group in bundle.catalog.list_volume_groups()}
    return {
        "archivedFileCount": len(instances),
        "usedBytes": int(getattr(cartridge, "used_bytes", 0) or 0) if cartridge else 0,
        "capacityBytes": int(getattr(cartridge, "capacity_bytes", 0) or 0) if cartridge else 0,
        "volumeGroup": groups.get(getattr(cartridge, "volume_group_id", None)) if cartridge else None,
        "formatted": bool(getattr(cartridge, "formatted", False)) if cartridge else False,
        "samplePaths": sorted(str(instance.tape_path) for instance in instances)[:_MAX_CANDIDATES],
    }


def _plan_format_tape(bundle: MediaBundle, *, barcode: object) -> JSONDict:
    """Phase one: run the real dry run and mint the real one-time safety token.

    This is not a rehearsal of the two-phase flow, it *is* the two-phase flow —
    ``FormatService.dry_run`` persists a ``SafetyToken`` row, and
    :func:`_format_tape` below can only succeed by presenting that token back to
    ``FormatService.confirm``, which validates it against the persisted row and
    deletes it. Skip this step and the format cannot happen at all; there is no
    branch in which a missing token merely downgrades the checks.
    """
    code = clean_barcode(barcode)
    # Refuse before minting a token: an unknown barcode should not leave a live
    # authorization sitting in the database for five minutes.
    _locate(bundle, code)
    plan, token = _run_service("format dry run", lambda: bundle.format_service.dry_run(code))
    remaining = max(0, int(getattr(token, "expires_at", time.time()) - time.time()))
    return {
        "barcode": code,
        "token": token.token,
        "tokenTtlSeconds": remaining,
        "operation": plan.operation,
        "isDestructive": bool(plan.is_destructive),
        "warnings": list(plan.warnings),
        # The backend reports no WORM bit through any interface the services
        # expose, and guessing one from a barcode suffix would be folklore
        # presented as fact on a destructive preview. Say what is true.
        "worm": None,
        "wouldWrite": "a new LTFS label, index partition and data partition",
        **_format_consequences(bundle, code),
    }


def _format_tape(bundle: MediaBundle, *, barcode: object, token: object = None) -> JSONDict:
    """Phase two: present the token from phase one to the confirm path.

    No token means no format. The service would refuse anyway (it looks the token
    up in ``safety_tokens``), but refusing here names the reason instead of
    surfacing "Unknown or missing safety token" from two layers down.
    """
    code = clean_barcode(barcode)
    token_value = "" if token is None else str(token).strip()
    if not token_value:
        raise MediaRefusedError(
            "This format has no safety token, which means the dry run did not run. "
            "The two-phase flow is not optional: plan the format again.",
            code="missing_safety_token",
        )
    _locate(bundle, code)
    result = _run_service("format", lambda: bundle.format_service.confirm(code, token_value))
    return {
        "barcode": code,
        "success": bool(result.success),
        "message": str(result.message),
        "tokenConsumed": True,
    }


# ---------------------------------------------------------------------------
# Bulk data: archive / restore
# ---------------------------------------------------------------------------


def _source_files(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(path for path in source.rglob("*") if path.is_file())


def _resolve_archive(bundle: MediaBundle, *, path: object, volume_group: object) -> JSONDict:
    source = clean_path(path, "source path")
    group_name = "" if volume_group is None else str(volume_group).strip()
    if not group_name:
        raise MediaRefusedError(
            "A volume group is required: the assistant does not choose which pool "
            "your data lands in.",
            code="missing_volume_group",
        )
    if not source.exists():
        raise MediaRefusedError(
            f"There is nothing at {source}.", code="source_not_found"
        )
    group = bundle.catalog.get_volume_group(group_name)
    if group is None:
        raise MediaRefusedError(
            f"There is no volume group named {group_name!r}.",
            code="unknown_volume_group",
            candidates=tuple(
                sorted(item.name for item in bundle.catalog.list_volume_groups())
            )[:_MAX_CANDIDATES],
        )
    files = _source_files(source)
    if not files:
        raise MediaRefusedError(
            f"{source} contains no files to archive.", code="empty_source"
        )
    if len(files) > MAX_ARCHIVE_FILES:
        raise MediaRefusedError(
            f"{source} holds {len(files)} files; one confirmed assistant action "
            f"archives at most {MAX_ARCHIVE_FILES}. Run "
            f"`openblade archive --volume-group {group_name} --path {source}` instead.",
            code="too_many_files",
        )
    cartridges = list(group.cartridges)
    return {
        "sourcePath": str(source),
        "volumeGroup": group.name,
        "fileCount": len(files),
        "byteCount": sum(item.stat().st_size for item in files),
        "tapeCount": len(cartridges),
        "sampleFiles": [str(item) for item in files[:_MAX_CANDIDATES]],
    }


def _plan_archive_path(
    bundle: MediaBundle, *, path: object, volume_group: object = None
) -> JSONDict:
    return _resolve_archive(bundle, path=path, volume_group=volume_group)


def _catalog_paths_for(source: Path, group_name: str) -> list[str]:
    """Where each source file lands in the catalog — mirrors ``run_archive_job``."""
    paths: list[str] = []
    for item in _source_files(source):
        relative = item.name if source.is_file() else str(item.relative_to(source))
        paths.append(str(PurePosixPath("/") / group_name / relative))
    return paths


def _archive_path(bundle: MediaBundle, *, path: object, volume_group: object = None) -> JSONDict:
    resolved = _resolve_archive(bundle, path=path, volume_group=volume_group)
    source = Path(resolved["sourcePath"])
    group_name = str(resolved["volumeGroup"])
    expected = _catalog_paths_for(source, group_name)
    job = _run_service("archive", lambda: bundle.archive_service.enqueue(group_name, source))
    # Verified against the catalog, not assumed from a return value: the job that
    # says "completed" and the rows a restore would later read are two different
    # facts, and the operator is owed the second one.
    #
    # Stated precisely because the field name could overclaim: this counts catalog
    # rows that EXIST at these paths now, not rows this job created. Re-archiving a
    # path that was already catalogued would therefore report it as present even if
    # this run had contributed nothing. That is the right answer to "can I restore
    # it", which is the question the operator is actually asking, and it is why the
    # reported line says "in the catalog" rather than "written".
    stored = [record for record in (bundle.catalog.get_file_record(p) for p in expected) if record]
    return {
        "jobId": job.id,
        "state": job.state,
        "sourcePath": str(source),
        "volumeGroup": group_name,
        "filesArchived": len(stored),
        "filesExpected": len(expected),
        "bytesArchived": sum(int(record.size_bytes) for record in stored),
        "tapes": sorted({str(instance.barcode) for record in stored for instance in record.instances}),
        "allFilesInCatalog": len(stored) == len(expected),
        "error": job.error,
    }


def _restore_destination(dest: Path, catalog_path: str) -> Path:
    """The file ``run_restore_job`` will actually write.

    Mirrors that function exactly: an existing *directory* destination means "put
    the file inside it", anything else means "this is the file". Getting this wrong
    would make the overwrite check inspect a path nothing writes to, and the
    strongest confirmation in the system would be guarding the wrong file.
    """
    if dest.exists() and dest.is_dir():
        return dest / PurePosixPath(catalog_path).name
    return dest


def _resolve_restore(bundle: MediaBundle, *, path: object, dest: object) -> JSONDict:
    catalog_path = clean_catalog_path(path)
    destination = clean_path(dest, "destination path")
    record = bundle.catalog.get_file_record(catalog_path)
    if record is None:
        raise MediaRefusedError(
            f"Nothing is archived at {catalog_path}.",
            code="unknown_catalog_path",
            candidates=tuple(
                item.path
                for item in bundle.catalog.list_file_records(
                    str(PurePosixPath(catalog_path).parent)
                )
            )[:_MAX_CANDIDATES],
        )
    instances = list(record.instances)
    if not instances:
        raise MediaRefusedError(
            f"{catalog_path} has no copy on any tape to restore from.",
            code="no_instance",
        )
    final = _restore_destination(destination, catalog_path)
    if not final.parent.exists():
        raise MediaRefusedError(
            f"The destination directory {final.parent} does not exist.",
            code="destination_missing",
        )
    overwrite = final.exists()
    if overwrite and final.is_dir():
        raise MediaRefusedError(
            f"{final} is a directory, not a file the restore can replace.",
            code="destination_is_directory",
        )
    return {
        "catalogPath": catalog_path,
        "destinationPath": str(final),
        "sizeBytes": int(record.size_bytes),
        "tapes": sorted({str(instance.barcode) for instance in instances}),
        "overwrites": overwrite,
        "existingBytes": final.stat().st_size if overwrite else None,
    }


def _plan_restore_path(bundle: MediaBundle, *, path: object, dest: object = None) -> JSONDict:
    return _resolve_restore(bundle, path=path, dest=dest)


def _restore_path(bundle: MediaBundle, *, path: object, dest: object = None) -> JSONDict:
    resolved = _resolve_restore(bundle, path=path, dest=dest)
    final = Path(str(resolved["destinationPath"]))
    # The *resolved* file path, not the operator's argument: the service applies
    # the same "existing directory means put it inside" rule, so handing it the
    # already-resolved path makes the file we verified and the file it writes the
    # same file by construction rather than by both getting the rule right twice.
    job = _run_service(
        "restore", lambda: bundle.restore_service.enqueue(resolved["catalogPath"], final)
    )
    # ``run_restore_job`` quarantines a checksum mismatch by renaming the file and
    # raising, so a completed job plus a file of the right size at the right path
    # is the verification, not a flag we chose to trust.
    return {
        "jobId": job.id,
        "state": job.state,
        "catalogPath": resolved["catalogPath"],
        "destinationPath": str(final),
        "bytesRestored": final.stat().st_size if final.exists() else 0,
        "sizeMatches": final.exists() and final.stat().st_size == resolved["sizeBytes"],
        "checksumVerified": job.state == "completed",
        "overwrote": bool(resolved["overwrites"]),
        "error": job.error,
    }


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


def _library_state(bundle: MediaBundle) -> JSONDict:
    """Read helper, so a media tool can describe the library without a second proxy."""
    inventory = _snapshot(bundle)
    return {
        "drives": [
            {
                "driveId": int(drive.drive_id),
                "barcode": str(drive.barcode) if drive.barcode is not None else None,
                "serial": _drive_serial(bundle, int(drive.drive_id)),
                "mountState": _mount_state(drive),
            }
            for drive in inventory.drives
        ],
        "occupiedSlots": sum(1 for slot in inventory.slots if slot.barcode is not None),
        "freeSlots": sum(1 for slot in inventory.slots if slot.barcode is None),
        "barcodes": _known_barcodes(inventory),
    }


# name -> operation. The keys are the facade's entire surface: ``MediaFacade``
# resolves nothing else, including every attribute ``object`` normally provides.
MEDIA_OPERATIONS: dict[str, Callable[..., JSONDict]] = {
    "library_state": _library_state,
    "plan_load_tape": _plan_load_tape,
    "load_tape": _load_tape,
    "plan_unload_drive": _plan_unload_drive,
    "unload_drive": _unload_drive,
    "plan_move_tape": _plan_move_tape,
    "move_tape": _move_tape,
    "plan_format_tape": _plan_format_tape,
    "format_tape": _format_tape,
    "plan_archive_path": _plan_archive_path,
    "archive_path": _archive_path,
    "plan_restore_path": _plan_restore_path,
    "restore_path": _restore_path,
}

MEDIA_OPERATION_NAMES: frozenset[str] = frozenset(MEDIA_OPERATIONS)

# The operations that actually act on media. Spelled out so a reviewer can see the
# whole tier-2 acting surface in one line.
ACTING_OPERATIONS: frozenset[str] = frozenset(
    {"load_tape", "unload_drive", "move_tape", "format_tape", "archive_path", "restore_path"}
)

# The operations that destroy something. Format erases a cartridge; restore can
# overwrite a local file. Everything else adds or moves.
DESTRUCTIVE_OPERATIONS: frozenset[str] = frozenset({"format_tape", "restore_path"})


def _resolve_operation(target: object, name: str) -> Callable[..., JSONDict]:
    """Resolve an allowlisted name to the module operation, bound to the bundle.

    The closure over ``target`` would itself be readable through ``__closure__``,
    but the proxy wraps every callable it returns in a
    :class:`~openblade.assistant.readonly.SealedCall`, so what a tool actually
    receives answers ``()`` and refuses every attribute.
    """
    operation = MEDIA_OPERATIONS[name]

    def bound(**kwargs: Any) -> JSONDict:
        return operation(target, **kwargs)

    return bound


class MediaFacade(AllowlistProxy):
    """The tier-2 media surface handed to media tools, and nothing more."""

    def __init__(self, bundle: MediaBundle) -> None:
        AllowlistProxy.__init__(
            self,
            bundle,
            MEDIA_OPERATION_NAMES,
            "media",
            error=MediaFacadeViolationError,
            resolver=_resolve_operation,
        )


def media_bundle(
    *,
    catalog: ReadOnlyProxy,
    inventory: ReadOnlyProxy,
    catalog_repo: Any,
    library: Any,
    ltfs: Any,
    format_service: Any,
    archive_service: Any,
    restore_service: Any,
    drive_serials: Sequence[tuple[str, int]] = (),
) -> MediaBundle:
    return MediaBundle(
        catalog=catalog,
        inventory=inventory,
        catalog_repo=catalog_repo,
        library=library,
        ltfs=ltfs,
        format_service=format_service,
        archive_service=archive_service,
        restore_service=restore_service,
        drive_serials=tuple(drive_serials),
    )


def media_facade(bundle: MediaBundle) -> MediaFacade:
    """Wrap a live bundle in the tier-2 media facade."""
    return MediaFacade(bundle)
