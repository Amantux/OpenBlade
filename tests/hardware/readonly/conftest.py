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
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import pytest

from openblade.config import load_config
from openblade.domain.policies import RealHardwareGuard
from openblade.hardware.runner import CommandResult, SafeRunner
from openblade.hardware.safety import require_real_hardware

CORPUS_DIR = Path(__file__).resolve().parents[3] / "compatibility"


def read_corpus() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(CORPUS_DIR.rglob("*.json"))]


class ReadOnlyViolation(RuntimeError):
    """A read-only-lane test attempted an HTTP request or command that could mutate the rig."""


# The only non-GET requests the read-only lane may send: the session handshake.
AUTH_REQUESTS = frozenset({("POST", "/aml/users/login"), ("POST", "/aml/auth/logout")})


def check_appliance_request(method: str, path: str) -> None:
    """Raise ReadOnlyViolation unless ``method path`` is a GET or the login/logout call.

    The method is matched exactly (HTTP methods are case-sensitive), so ``"get"``
    is refused rather than normalised.
    """
    verb = method
    if not path.startswith("/"):
        raise ReadOnlyViolation(f"appliance path must be absolute, got {path!r}")
    if verb == "GET" or (verb, path) in AUTH_REQUESTS:
        return
    raise ReadOnlyViolation(f"read-only lane refuses {verb} {path}")


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


@dataclass
class ApplianceResponse:
    status: int
    body: Any


@dataclass
class ApplianceClient:
    """Minimal cookie-session HTTP client for the real appliance's AML API."""

    base_url: str
    user: str
    password: str
    insecure: bool = False
    _opener: urllib.request.OpenerDirector = field(init=False)

    def __post_init__(self) -> None:
        context = ssl.create_default_context()
        if self.insecure:
            # Opt-in via OPENBLADE_APPLIANCE_INSECURE=true for a rig whose
            # appliance only has its factory self-signed certificate.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=context),
        )

    def request(self, method: str, path: str, json_body: Any = None) -> ApplianceResponse:
        check_appliance_request(method, path)
        data = None if json_body is None else json.dumps(json_body).encode()
        req = urllib.request.Request(  # noqa: S310 - base URL is operator-set rig config, scheme checked in fixture
            self.base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(req, timeout=30) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = None
        return ApplianceResponse(status=status, body=body)

    def login(self) -> ApplianceResponse:
        return self.request(
            "POST", "/aml/users/login", {"name": self.user, "password": self.password}
        )

    def logout(self) -> ApplianceResponse:
        return self.request("POST", "/aml/auth/logout")


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
