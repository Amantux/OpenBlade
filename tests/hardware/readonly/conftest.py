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
from openblade.hardware.safety import require_real_hardware

CORPUS_DIR = Path(__file__).resolve().parents[3] / "compatibility"


def read_corpus() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(CORPUS_DIR.rglob("*.json"))]


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
def hardware_guard(real_hardware_guard) -> RealHardwareGuard:
    return require_real_hardware(load_config())
