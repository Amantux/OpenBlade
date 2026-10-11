"""Fixtures for the read-only real-hardware lane.

Gating mirrors tests/hardware/conftest.py: every test here requests
``real_hardware_guard`` (skips unless OPENBLADE_BACKEND=real and
OPENBLADE_REAL_HARDWARE_ENABLED=true). The appliance's AML web services are
reached at OPENBLADE_APPLIANCE_URL with OPENBLADE_APPLIANCE_USER /
OPENBLADE_APPLIANCE_PASSWORD; tests needing them skip with that reason when unset.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from openblade.config import load_config
from openblade.domain.policies import RealHardwareGuard

# Moved to openblade/hardware/appliance_client.py (shared with tools/hardware/
# preflight.py); re-exported so fixtures and RebootClient keep importing from here.
from openblade.hardware.appliance_client import (  # noqa: F401 - re-export façade for importers of this conftest
    AUTH_REQUESTS,
    ApplianceClient,
    ApplianceResponse,
    ReadOnlyViolation,
    check_appliance_request,
)
from openblade.hardware.runner import CommandResult, SafeRunner
from openblade.hardware.safety import require_real_hardware

CORPUS_DIR = Path(__file__).resolve().parents[3] / "compatibility"


def read_corpus() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(CORPUS_DIR.rglob("*.json"))]


_DEVICE = re.compile(r"/dev/[A-Za-z0-9_./:+-]+")
_LOG_PAGE = re.compile(r"(0x)?[0-9A-Fa-f]{1,2}")


def _is_device(arg: str) -> bool:
    return _DEVICE.fullmatch(arg) is not None and ".." not in arg


def _is_path(arg: str) -> bool:
    return arg.startswith("/") and ".." not in arg


def is_allowed_argv(argv: list[str]) -> bool:
    """Exact argv shapes the read-only lane may execute; anything else is refused.

    Positions are fixed so a flag can't be smuggled in (``sg_logs -R`` resets log
    pages; ``mtx -f dev load`` moves a cartridge).
    """
    match argv:
        case ["sg_inq", dev]:
            return _is_device(dev)
        case ["sg_logs", "-p", page, dev]:
            return _LOG_PAGE.fullmatch(page) is not None and _is_device(dev)
        case ["mtx", "-f", dev, "status"]:
            return _is_device(dev)
        case ["ls", *paths] if paths:
            return all(_is_path(p) for p in paths)
    return False


class ReadOnlyRunner(SafeRunner):
    """SafeRunner that only executes the read-only argv shapes in ``is_allowed_argv``."""

    def run(
        self,
        args: list[str],
        timeout: int | None = None,
        redact_args: list[int] | None = None,
    ) -> CommandResult:
        if isinstance(args, str) or not is_allowed_argv(list(args)):
            raise ReadOnlyViolation(f"read-only lane refuses command {args!r}")
        return super().run(args, timeout=timeout, redact_args=redact_args)


@pytest.fixture
def appliance(real_hardware_guard) -> ApplianceClient:
    url = os.environ.get("OPENBLADE_APPLIANCE_URL", "")
    user = os.environ.get("OPENBLADE_APPLIANCE_USER", "")
    password = os.environ.get("OPENBLADE_APPLIANCE_PASSWORD", "")
    if not (url and user and password):
        pytest.skip(
            "Appliance AML API not configured: set OPENBLADE_APPLIANCE_URL, "
            "OPENBLADE_APPLIANCE_USER and OPENBLADE_APPLIANCE_PASSWORD"
        )
    if not url.startswith(("https://", "http://")):
        pytest.fail(f"OPENBLADE_APPLIANCE_URL must be http(s)://, got scheme of {url[:8]!r}")
    insecure = os.environ.get("OPENBLADE_APPLIANCE_INSECURE") == "true"
    return ApplianceClient(base_url=url, user=user, password=password, insecure=insecure)


@pytest.fixture
def authed_appliance(appliance: ApplianceClient):
    resp = appliance.login()
    assert resp.status == 200, f"appliance login failed: HTTP {resp.status}"
    yield appliance
    appliance.logout()


@pytest.fixture
def readonly_runner() -> ReadOnlyRunner:
    return ReadOnlyRunner(dry_run=False)


@pytest.fixture
def hardware_guard(real_hardware_guard) -> RealHardwareGuard:
    return require_real_hardware(load_config())
