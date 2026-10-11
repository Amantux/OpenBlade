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
import subprocess  # argv-list only, shell=False — see _deploy()
import sys
import urllib.error
import urllib.request
from dataclasses import asdict

from openblade.config_validation import is_deployable, validate_config
from openblade.deploy import (
    InvalidDigestError,
    ReadOnlyProbe,
    Stage,
    StageResult,
    parse_image_digest,
    run_deploy_pipeline,
    run_read_only_appliance_checks,
    run_rollback,
)
from openblade.topology import is_healthy_topology, verify_topology


def _precheck() -> StageResult:
    findings = validate_config(os.environ)
    blocking = [f.code for f in findings if f.severity == "blocking"]
    ok = is_deployable(findings)
    detail = "config valid" if ok else f"blocking findings: {blocking}"
    return StageResult(Stage.PRECHECK, ok, detail, findings=blocking)


def _deploy(cmd: list[str] | None, env: dict[str, str] | None = None) -> StageResult:
    if not cmd:
        return StageResult(Stage.DEPLOY, True, "skipped (no --deploy-cmd)")
    # `cmd` is already an argv list (shlex.split of --deploy-cmd) and no shell=True:
    # an operator string can therefore never become shell syntax.
    completed = subprocess.run(cmd, check=False, env=env)
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
    return StageResult(
        Stage.POSTCHECK, ok, "topology OK" if ok else f"blocking: {blocking}", findings=blocking
    )


def _postcheck_live(base_url: str, *, read_only_appliance_checks: bool = False) -> StageResult:
    def raw_probe(method: str, path: str) -> int:
        req = urllib.request.Request(base_url.rstrip("/") + path, method=method)
        try:
            # The URL is operator-supplied (--postcheck-url), so the scheme is not
            # constrained here; this runs as a deliberate deploy-time probe from the
            # operator's own shell, never on behalf of a request.
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:  # noqa: BLE001 - unreachable target is a failing probe
            return 599

    # A live probe cannot introspect the remote AppContext, so context checks are
    # satisfied by _AllWired and only endpoint reachability is meaningful. Pass no
    # emulator URLs so the fleet check surfaces its honest "unverified" warning
    # rather than falsely reporting the remote fleet as configured.
    # In read-only appliance mode every request goes through ReadOnlyProbe, so a
    # non-GET topology probe (e.g. the login POST) is never sent; it is reported.
    probe = ReadOnlyProbe(raw_probe) if read_only_appliance_checks else raw_probe
    findings = verify_topology(probe=probe, context=_AllWired(), emulator_urls=[])
    ok = is_healthy_topology(findings)
    blocking = [f.code for f in findings if f.severity == "blocking"]
    detail = "live topology OK" if ok else f"blocking: {blocking}"
    if isinstance(probe, ReadOnlyProbe):
        appliance = run_read_only_appliance_checks(probe)
        ok = ok and appliance.ok
        blocking += appliance.findings
        detail += f"; {appliance.detail}; not sent (non-GET): {probe.skipped}"
    return StageResult(Stage.POSTCHECK, ok, detail, findings=blocking)


class _AllWired:
    """Stand-in context whose members are all present (live probe checks endpoints, not internals)."""

    def __getattr__(self, _name: str) -> object:
        return self


def _print_report(results: list[StageResult], as_json: bool) -> None:
    if as_json:
        print(json.dumps([asdict(r) | {"stage": r.stage.value} for r in results], indent=2))
        return
    for r in results:
        print(f"  {'✓' if r.ok else '✗'} {r.stage.value}: {r.detail}")


def postcheck_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="deploy.py postcheck")
    ap.add_argument("--base-url", required=True, help="the deployed appliance to verify")
    ap.add_argument(
        "--read-only-appliance-checks",
        action="store_true",
        help="also GET /inventory and /jobs/recovery; never sends a non-GET request",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result = _postcheck_live(
        args.base_url, read_only_appliance_checks=args.read_only_appliance_checks
    )
    _print_report([result], args.json)
    return 0 if result.ok else 1


def rollback_main(argv: list[str]) -> int:
    # Deliberately no option that touches tape state: rollback redeploys the
    # application image only (asserted by tests/unit/test_deploy_release.py).
    ap = argparse.ArgumentParser(prog="deploy.py rollback")
    ap.add_argument(
        "--to", required=True, help="previous image digest: sha256:<hex> or <image>@sha256:<hex>"
    )
    ap.add_argument(
        "--image", default="ghcr.io/amantux/openblade", help="image name for a bare digest"
    )
    ap.add_argument(
        "--deploy-cmd",
        required=True,
        help="redeploy command; receives OPENBLADE_IMAGE=<image>@<digest> in its environment",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        image_ref = parse_image_digest(args.to, args.image)
    except InvalidDigestError as exc:
        print(f"rollback refused: {exc}", file=sys.stderr)
        return 2
    cmd = shlex.split(args.deploy_cmd)
    result = run_rollback(
        image_ref=image_ref,
        redeploy=lambda ref: _deploy(cmd, env=os.environ | {"OPENBLADE_IMAGE": ref}),
        notify=lambda msg: print(msg, file=sys.stderr),
    )
    _print_report([result], args.json)
    return 0 if result.ok else 1


def main(argv: list[str]) -> int:
    if argv and argv[0] == "postcheck":
        return postcheck_main(argv[1:])
    if argv and argv[0] == "rollback":
        return rollback_main(argv[1:])
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy-cmd", help="deploy command, e.g. 'docker compose up -d'")
    ap.add_argument("--skip-deploy", action="store_true", help="run pre/post checks only")
    ap.add_argument("--base-url", help="verify a live deployment at this URL instead of in-process")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cmd = None if (args.skip_deploy or not args.deploy_cmd) else shlex.split(args.deploy_cmd)
    if not args.base_url and os.environ.get("OPENBLADE_ENV", "").strip().lower() == "production":
        print(
            "warning: in-process postcheck boots the app against OPENBLADE_DB_URL on this host; "
            "pass --base-url to verify a real deployment instead.",
            file=sys.stderr,
        )
    postcheck = (lambda: _postcheck_live(args.base_url)) if args.base_url else _postcheck_in_process

    report = run_deploy_pipeline(
        precheck=_precheck, deploy=lambda: _deploy(cmd), postcheck=postcheck
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"deploy: {'PROMOTED' if report.promoted else 'NOT PROMOTED'}")
        for r in report.results:
            print(f"  {'✓' if r.ok else '✗'} {r.stage.value}: {r.detail}")
    return 0 if report.promoted else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
