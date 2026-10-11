"""Scratch/sacrificial media allowlist — the single parser for barcode lists.

Used by the CI hardware lanes (``tools/hardware/allowlist.py``) and, in code, by
``FormatService`` on a real backend: only barcodes in ``OPENBLADE_SCRATCH_BARCODES``
may be formatted, and an empty allowlist refuses everything (fail closed).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

SCRATCH_BARCODES_ENV = "OPENBLADE_SCRATCH_BARCODES"

_SEPARATORS = re.compile(r"[\s,]+")


class AllowlistError(Exception):
    """The requested barcodes are not safe to use destructively."""


def parse_barcodes(raw: str | None) -> list[str]:
    """Split a comma/whitespace list into upper-cased, de-duplicated barcodes."""
    seen: dict[str, None] = {}
    for token in _SEPARATORS.split(raw or ""):
        if token:
            seen.setdefault(token.strip().upper(), None)
    return list(seen)


def format_barcodes(barcodes: Sequence[str]) -> str:
    """The normalized stdout form: comma-joined, no spaces."""
    return ",".join(barcodes)


def check_subset(requested: Sequence[str], allowlist: Sequence[str]) -> list[str]:
    """Return ``requested`` if it is a non-empty subset of a non-empty allowlist.

    Raises :class:`AllowlistError` naming the offenders otherwise.
    """
    if not allowlist:
        raise AllowlistError(
            "SACRIFICIAL_BARCODES is empty: no tape is approved for destructive use (fail closed)."
        )
    if not requested:
        raise AllowlistError("No barcodes requested (pass --barcodes or set BARCODES).")
    allowed = set(allowlist)
    offenders = [b for b in requested if b not in allowed]
    if offenders:
        raise AllowlistError(
            "Barcodes outside the sacrificial allowlist: "
            + ", ".join(offenders)
            + ". Refusing before touching hardware."
        )
    return list(requested)


def scratch_barcodes_from_env() -> list[str]:
    """The operator's scratch allowlist from ``OPENBLADE_SCRATCH_BARCODES``."""
    return parse_barcodes(os.environ.get(SCRATCH_BARCODES_ENV))


def is_scratch_barcode(barcode: str, allowlist: Sequence[str]) -> bool:
    """True only if ``barcode`` is in a NON-empty allowlist (fail closed)."""
    return bool(allowlist) and barcode.strip().upper() in set(allowlist)
