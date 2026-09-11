"""Read-only access proxies for the assistant's tool layer.

The safety line for ``openblade assist`` is absolute: the assistant reads, explains
and *proposes* commands, and never performs a mutating or hardware-moving
operation. That is enforced structurally in three independent places, of which this
module is one:

1. :mod:`openblade.assistant.readonly` (here) — tools never touch a live
   ``CatalogRepository`` or ``LibraryBackend``. They see a proxy whose attribute
   allowlist is a literal set of read method names; anything else raises
   :class:`ReadOnlyViolationError` rather than returning a bound method.
2. :mod:`openblade.assistant.tools` — the tool registry refuses to build unless
   every registered name is on ``READ_ONLY_TOOL_NAMES``. Adding a tool without
   amending the allowlist fails closed.
3. :mod:`openblade.assistant.session` — the loop only ever calls registry handlers;
   it has no subprocess, no commit, and no write path of its own.

The allowlists below are deliberately spelled out. A future read method has to be
added here on purpose, and a future *write* method cannot be reached by accident.
"""

from __future__ import annotations

from typing import Any

from openblade.assistant.errors import ReadOnlyViolationError

# Catalog reads the assistant is allowed to perform. Every name here must be a
# pure query: no INSERT/UPDATE/DELETE, no flush, no commit.
CATALOG_READ_METHODS: frozenset[str] = frozenset(
    {
        "list_volume_groups",
        "get_volume_group",
        "list_cartridges",
        "get_cartridge",
        "list_jobs",
        "get_job",
        "list_file_records",
        "get_file_record",
        "list_instances_for_barcode",
        "list_catalog_tape_barcodes",
    }
)

# Library-backend reads. ``inventory()`` is the only non-moving call on the
# backend protocol; load/unload/move are media-moving and must never be reachable.
LIBRARY_READ_METHODS: frozenset[str] = frozenset({"inventory"})


class ReadOnlyProxy:
    """Attribute-allowlisting wrapper.

    Only names in ``allowed`` are forwarded to the wrapped object. Everything else
    raises, including private attributes and dunder lookups made through
    ``getattr`` — there is no escape hatch back to the live object.
    """

    __slots__ = ("_allowed", "_label", "_target")

    def __init__(self, target: object, allowed: frozenset[str], label: str) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_label", label)

    def __getattr__(self, name: str) -> Any:
        allowed: frozenset[str] = object.__getattribute__(self, "_allowed")
        label: str = object.__getattribute__(self, "_label")
        if name not in allowed:
            raise ReadOnlyViolationError(
                f"{label}.{name} is not a permitted read operation for the assistant"
            )
        target = object.__getattribute__(self, "_target")
        return getattr(target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        label: str = object.__getattribute__(self, "_label")
        raise ReadOnlyViolationError(f"{label} is read-only; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        label: str = object.__getattribute__(self, "_label")
        raise ReadOnlyViolationError(f"{label} is read-only; cannot delete {name!r}")

    def __repr__(self) -> str:
        label: str = object.__getattribute__(self, "_label")
        return f"<ReadOnlyProxy {label}>"


def read_only_catalog(catalog: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(catalog, CATALOG_READ_METHODS, "catalog")


def read_only_library(library: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(library, LIBRARY_READ_METHODS, "library")
