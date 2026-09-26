"""Narrowing helpers for values that arrive as ``object``/``Any``.

Decoded JSON bodies, add-on style config dicts and serialized catalog rows all
reach the code as ``dict[str, object]``, so every numeric field read needs to be
narrowed before ``int()`` will accept it. Four modules grew their own private
copy of the same three lines; this is the single version.

Nothing here changes a failure mode: each helper accepts exactly the argument
types the builtin accepts and raises the same exception for everything else, so
callers that already turn ``TypeError``/``ValueError`` into a 422 or a curated
domain error keep doing so.
"""

from __future__ import annotations

# The argument types `int()` actually accepts, restricted to what a decoded JSON
# document can produce (bool is an int subclass, so `int(True) == 1` as before).
_INT_LIKE = (int, float, str, bytes, bytearray)


def coerce_int(value: object) -> int:
    """``int(value)`` for an untyped value, with identical success and failure.

    Raises ``TypeError`` for anything ``int()`` could not have accepted and
    propagates ``ValueError`` from an unparseable string, exactly as ``int()``
    does.
    """
    if isinstance(value, _INT_LIKE):
        return int(value)
    raise TypeError(f"expected an int-like value, got {type(value).__name__}")
