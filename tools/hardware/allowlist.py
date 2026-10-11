"""Sacrificial-barcode allowlist gate for the destructive hardware lane.

Run BEFORE any hardware is touched::

    python -m tools.hardware.allowlist --barcodes "TST001L8 TST002L8"

The allowlist comes from env ``SACRIFICIAL_BARCODES``; the request comes from
``--barcodes`` or, when the flag is absent, env ``BARCODES``. Both accept
comma- and/or whitespace-separated lists (case-insensitive). Exit 0 when the
request is a non-empty subset of a non-empty allowlist and print exactly one
stdout line: the normalized request (upper-cased, de-duplicated, request order,
comma-joined, e.g. ``TST001L8,TST002L8``), suitable for
``OPENBLADE_SCRATCH_BARCODES``. Exit 2 otherwise (fail closed), naming every
offending barcode on stderr with nothing on stdout. All diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

# Parsing lives in the domain so the service-side gate and this CI tool share it.
from openblade.domain.media_allowlist import (
    AllowlistError,
    check_subset,
    format_barcodes,
    parse_barcodes,
)

EXIT_OK = 0
EXIT_VIOLATION = 2


__all__ = ["AllowlistError", "check_subset", "format_barcodes", "main", "parse_barcodes"]


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
    print(f"allowlist: OK: {len(approved)} barcode(s) approved", file=sys.stderr)
    print(format_barcodes(approved))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
