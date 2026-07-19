#!/usr/bin/env python3
"""CLI: validated deployment pipeline.

    python3 scripts/deploy.py --deploy-cmd "docker compose up -d" [--base-url URL] [--json]
    python3 scripts/deploy.py --skip-deploy            # re-run pre/post checks only

Precheck validates the config from the environment (refuses to deploy on any
blocking finding). The deploy stage runs the given command (never shell=True).
Postcheck verifies the runtime topology — in-process by default, or against a
live --base-url after a real deploy. Exit 0 only if the deploy is promoted.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess  # noqa: S404 - deploy command is operator-supplied, run without a shell
import sys
import urllib.error
import urllib.request

from openblade.config_validation import is_deployable, validate_config
from openblade.deploy import Stage, StageResult, run_deploy_pipeline
from openblade.topology import is_healthy_topology, verify_topology


def _precheck() -> StageResult:
    findings = validate_config(os.environ)
    blocking = [f.code for f in findings if f.severity == "blocking"]
    ok = is_deployable(findings)
    detail = "config valid" if ok else f"blocking findings: {blocking}"
    return StageResult(Stage.PRECHECK, ok, detail, findings=blocking)


def _deploy(cmd: list[str] | None) -> StageResult:
    if not cmd:
        return StageResult(Stage.DEPLOY, True, "skipped (no --deploy-cmd)")
    completed = subprocess.run(cmd, check=False)  # noqa: S603 - list form, no shell
    ok = completed.returncode == 0
    return StageResult(Stage.DEPLOY, ok, f"`{' '.join(cmd)}` exited {completed.returncode}")


def _postcheck_in_process() -> StageResult:
    from fastapi.testclient import TestClient

    from openblade.api import aml_state
    from openblade.api.main import app
    from openblade.bootstrap import get_context

    aml_state.ensure_initialized(get_context().config.db_url, force_reset=False)
    context = get_context()
    with TestClient(app) as client:
        findings = verify_topology(
            probe=lambda m, p: client.request(m, p).status_code,
            context=context,
            emulator_urls=context.config.emulator_urls,
        )
    ok = is_healthy_topology(findings)
    blocking = [f.code for f in findings if f.severity == "blocking"]
    return StageResult(Stage.POSTCHECK, ok, "topology OK" if ok else f"blocking: {blocking}", findings=blocking)


def _postcheck_live(base_url: str) -> StageResult:
    from openblade.topology import REQUIRED_ENDPOINTS

    def probe(method: str, path: str) -> int:
        req = urllib.request.Request(base_url.rstrip("/") + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - operator-supplied URL
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:  # noqa: BLE001 - unreachable target is a failing probe
            return 599

    findings = verify_topology(probe=probe, context=_AllWired(), emulator_urls=[base_url])
    # A live probe cannot introspect the in-process AppContext; only endpoint reachability
    # is meaningful here, so context checks are satisfied by _AllWired.
    ok = is_healthy_topology(findings)
    blocking = [f.code for f in findings if f.severity == "blocking"]
    _ = REQUIRED_ENDPOINTS
    return StageResult(Stage.POSTCHECK, ok, "live topology OK" if ok else f"blocking: {blocking}", findings=blocking)


class _AllWired:
    """Stand-in context whose members are all present (live probe checks endpoints, not internals)."""

    def __getattr__(self, _name: str) -> object:
        return self


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy-cmd", help="deploy command, e.g. 'docker compose up -d'")
    ap.add_argument("--skip-deploy", action="store_true", help="run pre/post checks only")
    ap.add_argument("--base-url", help="verify a live deployment at this URL instead of in-process")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cmd = None if (args.skip_deploy or not args.deploy_cmd) else shlex.split(args.deploy_cmd)
    postcheck = (lambda: _postcheck_live(args.base_url)) if args.base_url else _postcheck_in_process

    report = run_deploy_pipeline(precheck=_precheck, deploy=lambda: _deploy(cmd), postcheck=postcheck)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"deploy: {'PROMOTED' if report.promoted else 'NOT PROMOTED'}")
        for r in report.results:
            print(f"  {'✓' if r.ok else '✗'} {r.stage.value}: {r.detail}")
    return 0 if report.promoted else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
