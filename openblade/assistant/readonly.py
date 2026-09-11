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

Implementation note — why ``__getattribute__`` and an external state map:
    An earlier version stored the wrapped object in ``self._target`` (with
    ``__slots__``) and filtered through ``__getattr__``. That leaked. ``__getattr__``
    only runs when normal lookup *fails*, and slot descriptors are found by normal
    lookup, so ``proxy._target`` handed back the live repository — and
    ``proxy.__init__(evil, ...)`` re-pointed the proxy entirely, because ``__init__``
    is reachable by normal lookup too. The proxy now holds no instance state at all:
    the target lives in a module-private :class:`~weakref.WeakKeyDictionary`, and
    *every* attribute access goes through the allowlist, dunders included.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from openblade.assistant.errors import ReadOnlyViolationError

# Catalog reads the assistant is allowed to perform. Every name here must be a
# pure query: no INSERT/UPDATE/DELETE, no flush, no commit.
#
# ``expire_all`` is the one apparent outlier and is deliberate: it drops the
# SQLAlchemy identity map so the next read sees what other processes have
# committed. It writes nothing; without it a long-lived REPL confidently reports
# a job as "pending" minutes after it finished.
CATALOG_READ_METHODS: frozenset[str] = frozenset(
    {
        "list_volume_groups",
        "get_volume_group",
        "list_cartridges",
        "get_cartridge",
        "list_jobs",
        "get_job",
        "list_file_records",
        "list_catalog_files",
        "get_file_record",
        "list_instances_for_barcode",
        "list_catalog_tape_barcodes",
    }
)

# Inventory reads. The assistant is given ``InventoryService``, never the raw
# ``LibraryBackend`` -- so load/unload/move/eject are not merely un-allowlisted,
# they are not on the wrapped object at all.
#
# This also honours SAFETY_003 (``openblade/safety/import_guard.py``), which
# forbids a direct ``inventory()`` call on a library backend outside the
# authorized hardware access points (spelled indirectly here: the guard is a
# line-based substring scan, so writing the literal would trip it).
# The service layer already existed and the first version of this module simply
# bypassed it; going through ``snapshot()`` is the supported call path.
INVENTORY_READ_METHODS: frozenset[str] = frozenset({"snapshot"})

_State = tuple[object, frozenset[str], str]

# Proxy state lives here, not on the instance, so no attribute lookup on the proxy
# can reach it. Keyed weakly so a discarded proxy does not pin its target.
_PROXY_STATE: WeakKeyDictionary[Any, _State] = WeakKeyDictionary()


class ReadOnlyProxy:
    """Attribute-allowlisting wrapper with no reachable instance state.

    Only names in ``allowed`` are forwarded to the wrapped object. Everything else
    raises — private names, ``__class__``, ``__init__``, ``__dict__``,
    ``__reduce__`` and the rest of the ``object`` surface included — so there is no
    path from a tool body back to the live object.
    """

    def __init__(self, target: object, allowed: frozenset[str], label: str) -> None:
        # Reached only through ``type.__call__`` at construction; a later
        # ``proxy.__init__(...)`` is refused by ``__getattribute__`` below.
        _PROXY_STATE[self] = (target, allowed, label)

    def __getattribute__(self, name: str) -> Any:
        target, allowed, label = _PROXY_STATE[self]
        if name not in allowed:
            raise ReadOnlyViolationError(
                f"{label}.{name} is not a permitted read operation for the assistant"
            )
        return getattr(target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        _, _, label = _PROXY_STATE[self]
        raise ReadOnlyViolationError(f"{label} is read-only; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        _, _, label = _PROXY_STATE[self]
        raise ReadOnlyViolationError(f"{label} is read-only; cannot delete {name!r}")

    def __repr__(self) -> str:
        # Implicit special-method lookup uses the type slot, not
        # ``__getattribute__``, so this works while ``proxy.__repr__`` does not.
        _, _, label = _PROXY_STATE[self]
        return f"<ReadOnlyProxy {label}>"


def read_only_catalog(catalog: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(catalog, CATALOG_READ_METHODS, "catalog")


def read_only_inventory(inventory_service: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(inventory_service, INVENTORY_READ_METHODS, "inventory")
