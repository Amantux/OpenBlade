#!/usr/bin/env python3
"""CLI: validate OpenBlade configuration for the current environment.

Exit 0 = deployable; exit 1 = a BLOCKING finding (unsafe/incomplete production
config). Used by CI (`operability.yml`) and as a pre-deployment gate.

    OPENBLADE_ENV=production python3 scripts/validate_config.py
    python3 scripts/validate_config.py --json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from openblade.config import load_config
from openblade.config_validation import BLOCKING, is_deployable, validate_config


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    findings = validate_config(os.environ)

    # Also confirm the config actually loads (shape validity).
    load_error: str | None = None
    try:
        load_config()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the gate
        load_error = str(exc)

    deployable = is_deployable(findings) and load_error is None
    if as_json:
        print(json.dumps({
            "environment": os.environ.get("OPENBLADE_ENV", "development"),
            "deployable": deployable,
            "load_error": load_error,
            "findings": [asdict(f) for f in findings],
        }, indent=2))
    else:
        env = os.environ.get("OPENBLADE_ENV", "development")
        print(f"config validation ({env}): {'DEPLOYABLE' if deployable else 'BLOCKED'}")
        if load_error:
            print(f"  [blocking] config failed to load: {load_error}")
        for f in findings:
            mark = "✗" if f.severity == BLOCKING else "~"
            print(f"  {mark} [{f.severity}] {f.code}: {f.message}")
        if not findings and not load_error:
            print("  no issues.")
    return 0 if deployable else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
