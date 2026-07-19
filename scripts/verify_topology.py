#!/usr/bin/env python3
"""CLI: verify OpenBlade runtime topology (in-process, no ports opened).

Probes the required endpoints and asserts the in-process job worker + services +
backends are wired and the fleet is configured. Exit 0 = topology OK; exit 1 = a
BLOCKING gap. Used by CI and as a post-deploy check (proves topology *functions*).

    python3 scripts/verify_topology.py [--json]
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict

from fastapi.testclient import TestClient

from openblade.api import aml_state
from openblade.api.main import app
from openblade.bootstrap import get_context
from openblade.topology import BLOCKING, is_healthy_topology, verify_topology


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    aml_state.ensure_initialized(get_context().config.db_url, force_reset=False)
    context = get_context()

    with TestClient(app) as client:
        def probe(method: str, path: str) -> int:
            return client.request(method, path).status_code

        findings = verify_topology(
            probe=probe, context=context, emulator_urls=context.config.emulator_urls
        )
    ok = is_healthy_topology(findings)

    if as_json:
        print(json.dumps({"topology_ok": ok, "findings": [asdict(f) for f in findings]}, indent=2))
    else:
        print(f"topology: {'OK' if ok else 'DEGRADED'}")
        for f in findings:
            mark = "✗" if f.severity == BLOCKING else "~"
            print(f"  {mark} [{f.severity}] {f.code}: {f.message}")
        if not findings:
            print("  all required endpoints/consumers/services wired and responding.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
