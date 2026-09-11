"""Attribute-allowlisting access proxies for the assistant's tool layer.

The safety line for ``openblade assist``: the assistant reads, explains and
*proposes*, and the only thing it can execute is a tier-1 setup action the operator
confirmed in the REPL (:mod:`openblade.assistant.setup_facade`). Media movement,
format, archive, restore and delete are propose-only, as they always were. That is
enforced structurally in three independent places, of which this module is one:

1. :mod:`openblade.assistant.readonly` (here) — tools never touch a live
   ``CatalogRepository`` or ``LibraryBackend``. They see a proxy whose attribute
   allowlist is a literal set of read method names; anything else raises
   :class:`ReadOnlyViolationError` rather than returning a bound method. The same
   no-instance-state machinery backs the narrow *write* facade, which allowlists two
   catalog-only setup operations and nothing else.
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

from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary

from openblade.assistant.errors import AssistantError, ReadOnlyViolationError

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

Resolver = Callable[[object, str], Any]

_State = tuple[object, frozenset[str], str, type[AssistantError], Resolver]

# Proxy state lives here, not on the instance, so no attribute lookup on the proxy
# can reach it. Keyed weakly so a discarded proxy does not pin its target.
_PROXY_STATE: WeakKeyDictionary[Any, _State] = WeakKeyDictionary()


# State for sealed callables, off the object for the same reason the proxy's is.
_CALL_STATE: WeakKeyDictionary[Any, tuple[Any, str, type[AssistantError]]] = WeakKeyDictionary()


class SealedCall:
    """A callable that answers ``()`` and nothing else.

    A bound method advertises its receiver (``__self__``) and a closure advertises
    its cell contents (``__closure__``); either one hands the live repository to
    whoever holds the callable, which would make an attribute allowlist pointless.
    This wrapper forwards the call and refuses every attribute. ``__call__`` and
    ``__repr__`` still work because implicit special-method lookup goes through the
    type, not ``__getattribute__``.
    """

    def __init__(self, function: Any, label: str, error: type[AssistantError]) -> None:
        _CALL_STATE[self] = (function, label, error)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        function, _, _ = _CALL_STATE[self]
        return function(*args, **kwargs)

    def __getattribute__(self, name: str) -> Any:
        _, label, error = _CALL_STATE[self]
        raise error(f"{label} is a sealed call; {name!r} is not readable on it")

    def __setattr__(self, name: str, value: Any) -> None:
        _, label, error = _CALL_STATE[self]
        raise error(f"{label} is a sealed call; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        _, label, error = _CALL_STATE[self]
        raise error(f"{label} is a sealed call; cannot delete {name!r}")

    def __repr__(self) -> str:
        _, label, _ = _CALL_STATE[self]
        return f"<sealed call {label}>"


class AllowlistProxy:
    """Attribute-allowlisting wrapper with no reachable instance state.

    Only names in ``allowed`` resolve. Everything else raises — private names,
    ``__class__``, ``__init__``, ``__dict__``, ``__reduce__`` and the rest of the
    ``object`` surface included — so there is no path from a tool body back to the
    wrapped object.

    ``resolver`` decides what an allowlisted name resolves *to*. The default hands
    back the target's method, which is what a read proxy wants. The setup facade
    passes its own resolver so that an allowlisted name resolves to an operation
    defined in :mod:`openblade.assistant.setup_facade` — the target's own methods
    are then not reachable under any name at all.

    Whatever the resolver returns, a *callable* is wrapped in a
    :class:`SealedCall` before it leaves. Without that the allowlist is decorative
    for anyone holding a returned callable: ``proxy.list_volume_groups.__self__``
    hands back the live ``CatalogRepository`` — with its write methods on it —
    and a closure leaks the same thing through ``__closure__``. Found by attacking
    this module rather than by a failing test, so it is now covered by one.
    """

    def __init__(
        self,
        target: object,
        allowed: frozenset[str],
        label: str,
        *,
        error: type[AssistantError] = ReadOnlyViolationError,
        resolver: Resolver = getattr,
    ) -> None:
        # Reached only through ``type.__call__`` at construction. ``proxy.__init__``
        # is refused by ``__getattribute__`` below, and the unbound form —
        # ``AllowlistProxy.__init__(proxy, evil, ...)``, which bypasses instance
        # attribute lookup entirely — is refused here: state binds exactly once.
        if self in _PROXY_STATE:
            raise ReadOnlyViolationError(
                "This proxy is already bound; it cannot be re-pointed at another object"
            )
        _PROXY_STATE[self] = (target, allowed, label, error, resolver)

    def __getattribute__(self, name: str) -> Any:
        target, allowed, label, error, resolver = _PROXY_STATE[self]
        if name not in allowed:
            raise error(f"{label}.{name} is not a permitted operation for the assistant")
        resolved = resolver(target, name)
        if callable(resolved):
            return SealedCall(resolved, f"{label}.{name}", error)
        return resolved

    def __setattr__(self, name: str, value: Any) -> None:
        _, _, label, error, _ = _PROXY_STATE[self]
        raise error(f"{label} is sealed; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        _, _, label, error, _ = _PROXY_STATE[self]
        raise error(f"{label} is sealed; cannot delete {name!r}")

    def __repr__(self) -> str:
        # Implicit special-method lookup uses the type slot, not
        # ``__getattribute__``, so this works while ``proxy.__repr__`` does not.
        _, _, label, _, _ = _PROXY_STATE[self]
        return f"<{type(self).__name__} {label}>"


class ReadOnlyProxy(AllowlistProxy):
    """An :class:`AllowlistProxy` whose allowlist contains only read methods.

    Kept as a distinct type so "this object cannot write" is stated by the
    annotation, not only by the allowlist it happened to be built with.
    """


def read_only_catalog(catalog: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(catalog, CATALOG_READ_METHODS, "catalog")


def read_only_inventory(inventory_service: object) -> ReadOnlyProxy:
    return ReadOnlyProxy(inventory_service, INVENTORY_READ_METHODS, "inventory")
