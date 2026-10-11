"""Day-one appliance preflight: is this runner host ready for the hardware lanes?

READ-ONLY on hardware: it runs inquiries and listings only (``sg_inq``, ``mtx status``,
an appliance login/logout) and never moves, loads, formats or erases media. It must
never run armed, so it does not need (and warns about) ``OPENBLADE_REAL_HARDWARE_ENABLED``.

Usage::

    python -m tools.hardware.preflight --lane readonly|destructive [--json]

Exit 0 when no check FAILs, 1 otherwise.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

from openblade.config import parse_drive_serial_map
from openblade.domain.errors import DriveCorrelationError
from openblade.hardware.mtx import MtxStatus, parse_mtx_status
from openblade.hardware.runner import SafeRunner
from openblade.hardware.sg import parse_sg_inq
from tools.hardware.allowlist import parse_barcodes

Status = Literal["PASS", "WARN", "FAIL"]
Lane = Literal["readonly", "destructive"]

EXIT_OK = 0
EXIT_FAIL = 1

REQUIRED_TOOLS: tuple[str, ...] = ("sg_inq", "lsscsi", "mtx", "ltfs", "mkltfs")
BY_ID_DIR = "/dev/tape/by-id"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


class ApplianceProbe(Protocol):
    def login(self) -> int: ...
    def logout(self) -> int: ...


class PreflightError(Exception):
    """A curated, operator-facing failure (never carries raw upstream text)."""


class _UrllibAppliance:
    """Cookie-session login/logout against the appliance AML API (read-only)."""

    def __init__(self, base_url: str, user: str, password: str, *, insecure: bool) -> None:
        # Validate at the point of use: the URL arrives from runner env, not an API.
        if not base_url.startswith(("https://", "http://")):
            raise PreflightError("OPENBLADE_APPLIANCE_URL must start with http:// or https://")
        self._base = base_url.rstrip("/")
        self._user = user
        self._password = password
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        ]
        if insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self._opener = urllib.request.build_opener(*handlers)

    def _post(self, path: str, body: Mapping[str, str] | None) -> int:
        req = urllib.request.Request(  # noqa: S310 - scheme validated in __init__
            self._base + path,
            data=json.dumps(body or {}).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=15) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise PreflightError(f"appliance unreachable ({type(exc).__name__})") from exc

    def login(self) -> int:
        return self._post("/aml/users/login", {"name": self._user, "password": self._password})

    def logout(self) -> int:
        return self._post("/aml/auth/logout", None)


def _default_appliance(env: Mapping[str, str]) -> ApplianceProbe:
    return _UrllibAppliance(
        env.get("OPENBLADE_APPLIANCE_URL", ""),
        env.get("OPENBLADE_APPLIANCE_USER", ""),
        env.get("OPENBLADE_APPLIANCE_PASSWORD", ""),
        insecure=env.get("OPENBLADE_APPLIANCE_INSECURE") == "true",
    )


def _list_by_id() -> list[str]:
    try:
        return [os.path.realpath(e.path) + "|" + e.name for e in os.scandir(BY_ID_DIR)]
    except OSError:
        return []


@dataclass
class Host:
    """Everything the checks touch on the host — injected so unit tests use fakes."""

    env: Mapping[str, str]
    runner: SafeRunner
    which: Callable[[str], str | None] = shutil.which
    exists: Callable[[str], bool] = os.path.exists
    readable: Callable[[str], bool] = field(default=lambda p: os.access(p, os.R_OK))
    by_id: Callable[[], list[str]] = _list_by_id
    appliance: Callable[[Mapping[str, str]], ApplianceProbe] = _default_appliance


def _drives(env: Mapping[str, str]) -> list[str]:
    return [d.strip() for d in env.get("OPENBLADE_DRIVE_DEVICES", "").split(",") if d.strip()]


def check_armed(host: Host) -> Check:
    if host.env.get("OPENBLADE_REAL_HARDWARE_ENABLED"):
        return Check(
            "armed",
            "WARN",
            "OPENBLADE_REAL_HARDWARE_ENABLED is set in the preflight env",
            "unset it: preflight is read-only and must never run armed",
        )
    return Check("armed", "PASS", "not armed")


def check_env(host: Host, lane: Lane) -> list[Check]:
    required = [
        "OPENBLADE_CHANGER_DEVICE",
        "OPENBLADE_DRIVE_DEVICES",
        "OPENBLADE_APPLIANCE_URL",
        "OPENBLADE_APPLIANCE_USER",
        "OPENBLADE_APPLIANCE_PASSWORD",
    ]
    out: list[Check] = []
    for var in [*required, "SACRIFICIAL_BARCODES"]:
        if host.env.get(var, "").strip():
            # Presence only — values (notably the password) are never printed.
            out.append(Check(f"env:{var}", "PASS", "set"))
            continue
        hard = var in required or lane == "destructive"
        where = (
            "runner .env"
            if var.startswith("OPENBLADE_") and "APPLIANCE" not in var
            else "repo var/secret"
        )
        out.append(
            Check(
                f"env:{var}",
                "FAIL" if hard else "WARN",
                f"{var} is not set",
                f"set {var} ({where})",
            )
        )
    return out


def check_tools(host: Host) -> list[Check]:
    return [
        Check(f"tool:{t}", "PASS", host.which(t) or "")
        if host.which(t)
        else Check(
            f"tool:{t}", "FAIL", f"{t} not on PATH", "install sg3-utils / lsscsi / mtx / LTFS"
        )
        for t in REQUIRED_TOOLS
    ]


def check_devices(host: Host) -> list[Check]:
    changer = host.env.get("OPENBLADE_CHANGER_DEVICE", "").strip()
    drives = _drives(host.env)
    out: list[Check] = []
    for dev in ([changer] if changer else []) + drives:
        if not host.exists(dev):
            out.append(
                Check(f"device:{dev}", "FAIL", "does not exist", "fix the path in the runner .env")
            )
        elif not host.readable(dev):
            out.append(
                Check(
                    f"device:{dev}",
                    "FAIL",
                    "not readable",
                    "add the runner user to the tape+disk groups",
                )
            )
        else:
            out.append(Check(f"device:{dev}", "PASS", "exists, readable"))
    serials: list[str] = []
    for dev in drives:
        res = host.runner.run(["sg_inq", dev])
        inq = parse_sg_inq(res.stdout) if res.success else None
        if inq is None or not inq.serial:
            out.append(
                Check(
                    f"inquiry:{dev}",
                    "FAIL",
                    "sg_inq returned no serial",
                    "check cabling/permissions",
                )
            )
            serials.append("")
            continue
        out.append(
            Check(
                f"inquiry:{dev}",
                "PASS",
                f"{inq.vendor} {inq.product} {inq.revision} sn={inq.serial}",
            )
        )
        serials.append(inq.serial)
    real = [s for s in serials if s]
    dupes = sorted({s for s in real if real.count(s) > 1})
    if dupes:
        out.append(
            Check(
                "serials",
                "FAIL",
                f"duplicate drive serials {dupes}",
                "a drive is listed twice in OPENBLADE_DRIVE_DEVICES",
            )
        )
    elif real:
        out.append(Check("serials", "PASS", f"{len(real)} unique"))
    links = host.by_id()
    for serial in real:
        if not any(serial in link.rsplit("|", 1)[-1] for link in links):
            out.append(
                Check(
                    f"by-id:{serial}",
                    "FAIL",
                    f"no {BY_ID_DIR} entry",
                    "check udev rules for tape devices",
                )
            )
        else:
            out.append(Check(f"by-id:{serial}", "PASS", "resolves"))
    raw_map = host.env.get("OPENBLADE_DRIVE_SERIAL_MAP", "")
    if raw_map.strip():
        try:
            mapping = dict(parse_drive_serial_map(raw_map))
        except DriveCorrelationError:
            out.append(
                Check(
                    "serial-map",
                    "FAIL",
                    "OPENBLADE_DRIVE_SERIAL_MAP is malformed",
                    "use SERIAL:DTE,...",
                )
            )
            return out
        wrong = [
            f"{s}->{mapping.get(s)} (inquiry DTE {i})"
            for i, s in enumerate(serials)
            if s and mapping.get(s) != i
        ]
        if wrong:
            out.append(
                Check(
                    "serial-map",
                    "FAIL",
                    "disagrees with inquiry order: " + ", ".join(wrong),
                    "fix DTE order",
                )
            )
        else:
            out.append(Check("serial-map", "PASS", "agrees with inquiry order"))
    return out


def check_changer(host: Host) -> tuple[list[Check], MtxStatus | None]:
    changer = host.env.get("OPENBLADE_CHANGER_DEVICE", "").strip()
    if not changer:
        return [Check("changer", "FAIL", "no changer device", "set OPENBLADE_CHANGER_DEVICE")], None
    res = host.runner.run(["mtx", "-f", changer, "status"])
    if not res.success:
        return [
            Check(
                "changer", "FAIL", f"mtx status failed (rc={res.returncode})", "check the sg device"
            )
        ], None
    status = parse_mtx_status(res.stdout)
    want = len(_drives(host.env))
    got = status.drive_count or len(status.drives)
    if got != want:
        return [
            Check(
                "changer",
                "FAIL",
                f"changer reports {got} drives, env lists {want}",
                "fix OPENBLADE_DRIVE_DEVICES",
            )
        ], status
    return [
        Check("changer", "PASS", f"{got} drives, {status.slot_count or len(status.slots)} slots")
    ], status


def check_allowlist(host: Host, status: MtxStatus | None, lane: Lane) -> list[Check]:
    barcodes = parse_barcodes(host.env.get("SACRIFICIAL_BARCODES"))
    bad: Status = "FAIL" if lane == "destructive" else "WARN"
    if not barcodes:
        return [
            Check(
                "allowlist",
                bad,
                "SACRIFICIAL_BARCODES is empty",
                "set the repo var to scratch tapes",
            )
        ]
    if status is None:
        return [
            Check("allowlist", bad, "no inventory to check against", "fix the changer check first")
        ]
    loaded = {d.barcode.upper() for d in status.drives if d.loaded and d.barcode}
    present = loaded | {
        s.barcode.upper() for s in [*status.slots, *status.import_export_slots] if s.barcode
    }
    out: list[Check] = []
    missing = [b for b in barcodes if b not in present]
    in_drive = [b for b in barcodes if b in loaded]
    if missing:
        out.append(
            Check("allowlist", bad, f"not in inventory: {missing}", "fix SACRIFICIAL_BARCODES")
        )
    if in_drive:
        out.append(
            Check(
                "allowlist", bad, f"loaded in a drive: {in_drive}", "unload them via the UI first"
            )
        )
    if not out:
        out.append(Check("allowlist", "PASS", f"{len(barcodes)} barcode(s) in slots, none loaded"))
    return out


def check_appliance(host: Host) -> list[Check]:
    env = host.env
    out: list[Check] = []
    if env.get("OPENBLADE_APPLIANCE_INSECURE") == "true":
        out.append(
            Check(
                "appliance:tls",
                "WARN",
                "TLS verification disabled",
                "install the appliance CA instead",
            )
        )
    if not all(
        env.get(v)
        for v in (
            "OPENBLADE_APPLIANCE_URL",
            "OPENBLADE_APPLIANCE_USER",
            "OPENBLADE_APPLIANCE_PASSWORD",
        )
    ):
        out.append(
            Check("appliance", "FAIL", "appliance credentials incomplete", "see env:* checks")
        )
        return out
    try:
        client = host.appliance(env)
        code = client.login()
        if code != 200:
            out.append(
                Check("appliance", "FAIL", f"login HTTP {code}", "check user/password and the URL")
            )
            return out
        client.logout()
    except PreflightError as exc:
        out.append(
            Check("appliance", "FAIL", str(exc), "check OPENBLADE_APPLIANCE_URL and the network")
        )
        return out
    out.append(Check("appliance", "PASS", "login + logout OK"))
    return out


def run_preflight(host: Host, lane: Lane) -> list[Check]:
    checks = [check_armed(host), *check_env(host, lane), *check_tools(host), *check_devices(host)]
    changer_checks, status = check_changer(host)
    return [*checks, *changer_checks, *check_allowlist(host, status, lane), *check_appliance(host)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.hardware.preflight")
    parser.add_argument("--lane", choices=["readonly", "destructive"], required=True)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON on stdout")
    args = parser.parse_args(argv)
    lane: Lane = "destructive" if args.lane == "destructive" else "readonly"
    checks = run_preflight(Host(env=os.environ, runner=SafeRunner()), lane)
    failed = any(c.status == "FAIL" for c in checks)
    if args.json:
        print(
            json.dumps(
                {"lane": lane, "ok": not failed, "checks": [asdict(c) for c in checks]}, indent=2
            )
        )
    else:
        for c in checks:
            print(
                f"{c.status:4}  {c.name:32} {c.detail}"
                + (f"  -> {c.fix}" if c.fix and c.status != "PASS" else "")
            )
    return EXIT_FAIL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
