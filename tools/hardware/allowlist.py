"""Sacrificial-barcode allowlist gate for the destructive hardware lane.

Run BEFORE any hardware is touched::

    python -m tools.hardware.allowlist --barcodes "TST001L8 TST002L8"

The allowlist comes from env ``SACRIFICIAL_BARCODES``; the request comes from
``--barcodes`` or, when the flag is absent, env ``BARCODES``. Both accept
comma- and/or whitespace-separated lists. Exit 0 when the request is a
non-empty subset of a non-empty allowlist; exit 2 otherwise (fail closed),
naming every offending barcode.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence

EXIT_OK = 0
EXIT_VIOLATION = 2

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.hardware.allowlist")
    parser.add_argument(
        "--barcodes",
        default=None,
        help="Comma/space-separated barcodes to use (default: env BARCODES).",
    )
    args = parser.parse_args(argv)
    raw_request = args.barcodes if args.barcodes is not None else os.environ.get("BARCODES")
    try:
        approved = check_subset(
            parse_barcodes(raw_request),
            parse_barcodes(os.environ.get("SACRIFICIAL_BARCODES")),
        )
    except AllowlistError as exc:
        print(f"allowlist: REFUSED: {exc}", file=sys.stderr)
        return EXIT_VIOLATION
    print("allowlist: OK: " + " ".join(approved))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
