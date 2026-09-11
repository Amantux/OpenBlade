"""The narrow write facade — the assistant's *only* route to a catalog write.

The assistant is read-only everywhere except here. This module exposes exactly two
mutating operations, both catalog-only and both reversible by hand:

* ``new_volume_group`` — create an empty volume group (pool).
* ``attach_tapes`` — put existing cartridges into an existing volume group.

Both mirror an endpoint the REST API already serves (``POST /volume-groups/`` and
``POST /volume-groups/{name}/assign``) and call the same two repository methods. No
service capability is invented here: there is deliberately no *removal* operation,
because the catalog layer has none.

Nothing here moves media, formats, archives, restores or deletes — those remain
propose-only (tier 2), and there is no facade operation for them to hide behind.
The ``LibraryBackend`` is not reachable: the facade wraps the ``CatalogRepository``
and nothing else.

Two structural properties make that claim checkable rather than aspirational:

1. :class:`SetupFacade` is an :class:`~openblade.assistant.readonly.AllowlistProxy`
   with a custom resolver, so it holds no instance state and an allowlisted name
   resolves to an *operation defined in this module*, never to a bound method of the
   wrapped repository. ``facade.delete_file_record`` does not exist to be found;
   neither does ``facade._target`` nor ``facade.__init__``.
2. This is the only module in ``openblade/assistant`` allowed to name a catalog
   write method. ``tests/safety/test_assistant_read_only.py`` scans the package AST
   and fails if any other module does — which is why the operations are named
   ``new_volume_group``/``attach_tapes`` and not after the repository methods they
   call. A future removal operation must likewise be named in this module's
   vocabulary (``detach_tapes``), because the tool-name denylist in
   :mod:`openblade.assistant.setup_tools` rejects anything containing ``move``.

Ambiguity refuses even when confirmed. Each operation has a ``plan_*`` twin that
validates against the live catalog before the operator is asked, and the executing
operation validates *again* inside the write path — so an operator's "yes" can
never turn a guessed barcode into a write, even if the catalog changed in between.
:class:`SetupRefusedError` carries the candidates the operator plausibly meant.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from openblade.assistant.errors import SetupFacadeViolationError, SetupRefusedError
from openblade.assistant.readonly import AllowlistProxy

JSONDict = dict[str, Any]

# Longest name we will create. Volume-group names end up in CLI arguments and job
# metadata; a model that hallucinates a paragraph should be refused, not stored.
MAX_NAME_LENGTH = 64
# Most tapes one confirmed action may attach. A pool is built from a handful of
# cartridges; a request for fifty is a misunderstanding worth surfacing.
MAX_TAPES_PER_ACTION = 24
_MAX_CANDIDATES = 5


def clean_name(raw: object) -> str:
    """Normalize a volume-group name and refuse anything unusable.

    Control characters are rejected rather than stripped: a name arriving with an
    embedded newline is a log-forging attempt or a confused model, and silently
    "fixing" it would create an object the operator did not ask for.
    """
    name = str(raw or "").strip()
    if not name:
        raise SetupRefusedError("A volume group name is required.", code="missing_name")
    if len(name) > MAX_NAME_LENGTH:
        raise SetupRefusedError(
            f"That volume group name is longer than {MAX_NAME_LENGTH} characters.",
            code="invalid_name",
        )
    if not name.isprintable() or any(character.isspace() and character != " " for character in name):
        raise SetupRefusedError(
            "A volume group name must be a single printable line.", code="invalid_name"
        )
    return name


def clean_barcodes(raw: object) -> tuple[str, ...]:
    """Accept a list, a single string, or a comma/space separated string.

    Small models emit all three shapes for the same intent. Normalizing here keeps
    the confirmation preview and the executed action byte-identical, which is what
    makes the decline cache and the audit line trustworthy.
    """
    if raw is None:
        values: Sequence[Any] = ()
    elif isinstance(raw, str):
        values = [part for chunk in raw.split(",") for part in chunk.split()]
    elif isinstance(raw, Sequence):
        values = list(raw)
    else:
        values = ()

    seen: list[str] = []
    for value in values:
        barcode = str(value).strip().upper()
        if not barcode:
            continue
        if not barcode.isalnum():
            raise SetupRefusedError(
                f"{barcode!r} is not a barcode. Barcodes are alphanumeric, e.g. PH000001.",
                code="invalid_barcode",
            )
        if barcode not in seen:
            seen.append(barcode)
    if not seen:
        raise SetupRefusedError("At least one tape barcode is required.", code="missing_barcodes")
    if len(seen) > MAX_TAPES_PER_ACTION:
        raise SetupRefusedError(
            f"One action may add at most {MAX_TAPES_PER_ACTION} tapes; "
            f"{len(seen)} were requested.",
            code="too_many_tapes",
        )
    return tuple(seen)


def _group_names(catalog: Any) -> list[str]:
    return sorted(group.name for group in catalog.list_volume_groups())


def _barcode_candidates(catalog: Any, wanted: str) -> tuple[str, ...]:
    """Barcodes the operator plausibly meant, best first.

    Ranked by kind of evidence rather than a similarity score: a shared prefix beats
    a substring match beats "a tape that is currently in no pool". The unassigned
    tapes are included deliberately — a typo'd barcode usually means "the tape in my
    hand", and unassigned tapes are what a new pool is built from.
    """
    cartridges = catalog.list_cartridges()
    stem = wanted[:4]
    prefix = [item.barcode for item in cartridges if stem and item.barcode.startswith(stem)]
    contains = [
        item.barcode
        for item in cartridges
        if stem and stem in item.barcode and item.barcode not in prefix
    ]
    free = [item.barcode for item in cartridges if item.volume_group_id is None]
    ordered: list[str] = []
    for barcode in [*prefix, *contains, *free]:
        if barcode not in ordered:
            ordered.append(barcode)
    return tuple(ordered[:_MAX_CANDIDATES])


def _describe_group(group: Any) -> JSONDict:
    return {
        "id": group.id,
        "name": group.name,
        "barcodes": sorted(str(barcode) for barcode in group.barcodes),
        "tapeCount": len(list(group.cartridges)),
    }


# ---------------------------------------------------------------------------
# Validation, shared by each operation and its plan twin
# ---------------------------------------------------------------------------


def _validate_new_group(catalog: Any, name: object) -> str:
    cleaned = clean_name(name)
    existing = catalog.get_volume_group(cleaned)
    if existing is not None:
        # The REST API answers 409 here; refusing keeps the two surfaces honest and
        # stops "create it" from silently meaning "you already had one".
        raise SetupRefusedError(
            f"A volume group named {cleaned!r} already exists with "
            f"{len(list(existing.cartridges))} tape(s).",
            code="volume_group_exists",
            candidates=tuple(_group_names(catalog)[:_MAX_CANDIDATES]),
        )
    return cleaned


def _validate_attach(catalog: Any, name: object, barcodes: object) -> tuple[Any, list[str], list[str]]:
    """Resolve the group and partition the barcodes, refusing on any ambiguity."""
    cleaned = clean_name(name)
    wanted = clean_barcodes(barcodes)

    group = catalog.get_volume_group(cleaned)
    if group is None:
        raise SetupRefusedError(
            f"There is no volume group named {cleaned!r}.",
            code="unknown_volume_group",
            candidates=tuple(_group_names(catalog)[:_MAX_CANDIDATES]),
        )

    owners = {item.id: item.name for item in catalog.list_volume_groups()}
    already: list[str] = []
    to_add: list[str] = []
    for barcode in wanted:
        cartridge = catalog.get_cartridge(barcode)
        if cartridge is None:
            # Not merely "not found": the underlying repository method would happily
            # CREATE a row for an unknown barcode, inventing a tape that does not
            # physically exist. Refusing here is what keeps that unreachable.
            raise SetupRefusedError(
                f"No tape with barcode {barcode} is known to this library.",
                code="unknown_barcode",
                candidates=_barcode_candidates(catalog, barcode),
            )
        if cartridge.volume_group_id == group.id:
            already.append(barcode)
        elif cartridge.volume_group_id is not None:
            owner = owners.get(cartridge.volume_group_id, cartridge.volume_group_id)
            raise SetupRefusedError(
                f"Tape {barcode} is already in volume group {owner!r}. Moving a tape "
                "between pools is not something the assistant does; the operator can "
                "do it deliberately if that is really the intent.",
                code="tape_in_other_volume_group",
                candidates=(barcode,),
            )
        else:
            to_add.append(barcode)
    return group, to_add, already


# ---------------------------------------------------------------------------
# Operations. This module is the only one allowed to name a catalog write method.
# ---------------------------------------------------------------------------


def _known_volume_groups(catalog: Any) -> JSONDict:
    """Read helper, so a setup tool can describe state without a second proxy."""
    return {"volumeGroups": _group_names(catalog)}


def _plan_new_volume_group(catalog: Any, *, name: object) -> JSONDict:
    cleaned = _validate_new_group(catalog, name)
    return {"name": cleaned, "existingVolumeGroups": _group_names(catalog)}


def _new_volume_group(catalog: Any, *, name: object) -> JSONDict:
    cleaned = _validate_new_group(catalog, name)
    group = catalog.create_volume_group(cleaned)
    return {"created": True, **_describe_group(group)}


def _plan_attach_tapes(catalog: Any, *, name: object, barcodes: object) -> JSONDict:
    group, to_add, already = _validate_attach(catalog, name, barcodes)
    return {
        "name": group.name,
        "toAdd": to_add,
        "alreadyPresent": already,
        "currentTapeCount": len(list(group.cartridges)),
    }


def _attach_tapes(catalog: Any, *, name: object, barcodes: object) -> JSONDict:
    # Re-validated here, inside the write path: the plan ran before the operator
    # answered, and confirmation is not a licence to act on stale facts.
    group, to_add, already = _validate_attach(catalog, name, barcodes)
    for barcode in to_add:
        catalog.add_barcode_to_volume_group(group.id, barcode)
    # Membership is read back from the cartridges, not from the group's collection:
    # the repository commits with ``expire_on_commit=False``, so the already-loaded
    # ``group.cartridges`` would still report the pre-write state and the assistant
    # would confirm "0 tapes" immediately after adding two.
    members = sorted(
        str(item.barcode)
        for item in catalog.list_cartridges()
        if item.volume_group_id == group.id
    )
    return {
        "added": to_add,
        "alreadyPresent": already,
        "id": group.id,
        "name": group.name,
        "barcodes": members,
        "tapeCount": len(members),
    }


# name -> operation. The keys are the facade's entire surface: ``SetupFacade``
# resolves nothing else, including every attribute ``object`` normally provides.
SETUP_OPERATIONS: dict[str, Callable[..., JSONDict]] = {
    "known_volume_groups": _known_volume_groups,
    "plan_new_volume_group": _plan_new_volume_group,
    "new_volume_group": _new_volume_group,
    "plan_attach_tapes": _plan_attach_tapes,
    "attach_tapes": _attach_tapes,
}

SETUP_OPERATION_NAMES: frozenset[str] = frozenset(SETUP_OPERATIONS)

# The two operations that actually write. Spelled out so a reviewer can see the
# whole mutating surface of the assistant in one line.
WRITING_OPERATIONS: frozenset[str] = frozenset({"new_volume_group", "attach_tapes"})


def _resolve_operation(target: object, name: str) -> Callable[..., JSONDict]:
    """Resolve an allowlisted name to the module operation, bound to the catalog.

    The closure over ``target`` would itself be readable through ``__closure__``,
    but the proxy wraps every callable it returns in a
    :class:`~openblade.assistant.readonly.SealedCall`, so what a tool actually
    receives answers ``()`` and refuses every attribute.
    """
    operation = SETUP_OPERATIONS[name]

    def bound(**kwargs: Any) -> JSONDict:
        return operation(target, **kwargs)

    return bound


class SetupFacade(AllowlistProxy):
    """The tier-1 write surface handed to setup tools, and nothing more."""

    def __init__(self, catalog: object) -> None:
        AllowlistProxy.__init__(
            self,
            catalog,
            SETUP_OPERATION_NAMES,
            "setup",
            error=SetupFacadeViolationError,
            resolver=_resolve_operation,
        )


def setup_facade(catalog: object) -> SetupFacade:
    """Wrap a live ``CatalogRepository`` in the tier-1 write facade."""
    return SetupFacade(catalog)
