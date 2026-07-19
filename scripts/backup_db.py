#!/usr/bin/env python3
"""CLI: WAL-safe online backup of the OpenBlade SQLite catalog.

    python3 scripts/backup_db.py [--db sqlite:///path] --out backups/openblade-<tag>.db

Never touches production data destructively; produces a consistent snapshot copy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from openblade.dr import backup_sqlite


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("OPENBLADE_DB_URL"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    if not args.db:
        print("error: no --db and OPENBLADE_DB_URL unset", file=sys.stderr)
        return 2
    meta = backup_sqlite(args.db, Path(args.out))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
