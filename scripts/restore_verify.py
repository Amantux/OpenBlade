#!/usr/bin/env python3
"""CLI: executed restore verification (never writes to the source/production DB).

Backs up the source (or a self-seeded DB when none is given), restores into an
isolated temp DB, and verifies integrity + schema + a real data-layer read.

    python3 scripts/restore_verify.py [--db sqlite:///path] [--backup file] [--json]

Exit 0 = restore verified; exit 1 = a check failed. A REAL production-backup
restore requires supplying that backup artifact via --backup (pending external
prerequisite; see docs/disaster-recovery.md).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from openblade.dr import backup_sqlite, restore_and_verify


def _seed_source() -> str:
    from openblade.catalog.db import init_db
    path = Path(tempfile.mktemp(suffix=".db"))
    url = f"sqlite:///{path}"
    init_db(url)  # creates the full schema (create_all)
    return url


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--backup")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.backup:
        report = restore_and_verify(Path(args.backup))
    else:
        src_url = args.db or _seed_source()
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "backup.db"
            backup_sqlite(src_url, backup)
            report = restore_and_verify(backup)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"restore verification: {'PASS' if report.ok else 'FAIL'}")
        for c in report.checks:
            print(f"  {'✓' if c.ok else '✗'} {c.name}: {c.detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
