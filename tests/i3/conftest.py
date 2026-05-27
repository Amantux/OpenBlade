"""
Shared fixtures for the Quantum i3 test suite.

Env vars:
    I3_TEST_MODE              emulator (default) | real
    I3_AML_URL                base URL for AML API (default: http://localhost:8000)
    I3_AML_USER               AML username (default: admin)
    I3_AML_PASSWORD           AML password (default: password)
    I3_TIMING_PROFILE         instant | realistic | hardware
    I3_REAL_HARDWARE_ENABLED  safety gate — must be "true" to run real-i3 tests
"""
from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from tests.i3.timing import get_profile, get_profile_name


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "i3: Quantum i3 emulator + protocol tests")
    config.addinivalue_line("markers", "real_i3: tests that require a physical Quantum i3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _i3_base_url() -> str:
    return os.environ.get("I3_AML_URL", "http://localhost:8000").rstrip("/")


def _skip_if_real_mode_not_enabled() -> None:
    if os.environ.get("I3_TEST_MODE", "emulator") == "real":
        if os.environ.get("I3_REAL_HARDWARE_ENABLED", "false").lower() != "true":
            pytest.skip(
                "Real i3 mode requires I3_REAL_HARDWARE_ENABLED=true. "
                "Set both I3_TEST_MODE=real and I3_REAL_HARDWARE_ENABLED=true to proceed."
            )


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def i3_mode() -> str:
    return os.environ.get("I3_TEST_MODE", "emulator").strip().lower()


@pytest.fixture(scope="session")
def i3_base_url(i3_mode: str) -> str:
    return _i3_base_url()


@pytest.fixture(scope="session")
def timing_profile_name():
    return get_profile_name()


@pytest.fixture(scope="session")
def timing() -> dict[str, float]:
    return get_profile()


@pytest.fixture(scope="session")
def mode_guard(i3_mode: str) -> None:
    """Skip any test that needs real hardware if the safety gate is not open."""
    _skip_if_real_mode_not_enabled()


@pytest.fixture(scope="session")
def i3_credentials() -> tuple[str, str]:
    user = os.environ.get("I3_AML_USER", "admin")
    password = os.environ.get("I3_AML_PASSWORD", "password")
    return user, password


@pytest.fixture(scope="session")
def i3_client(i3_base_url: str) -> Generator[httpx.Client, None, None]:
    """HTTP client pointed at the AML API target (emulator or real i3).

    This client does NOT hold a session token — individual tests handle auth
    as needed, or use auth_headers fixture for pre-authenticated requests.
    """
    with httpx.Client(base_url=i3_base_url, timeout=120.0) as client:
        yield client


@pytest.fixture
def auth_headers(i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> dict[str, str]:
    """Return Authorization headers after logging in.

    Uses the AML /aml/auth/login endpoint. Returns Bearer token headers.
    Falls back to Basic auth if login endpoint is unavailable.
    """
    user, password = i3_credentials
    try:
        resp = i3_client.post("/aml/auth/login", json={"username": user, "password": password})
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token") or data.get("access_token") or data.get("sessionToken")
            if token:
                return {"Authorization": f"Bearer {token}"}
    except httpx.HTTPError:
        pass
    # Fall back to basic auth embedded in header
    import base64
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture
def fresh_auth_headers(i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> dict[str, str]:
    """Like auth_headers but always obtains a fresh token (for session-expiry tests)."""
    user, password = i3_credentials
    resp = i3_client.post("/aml/auth/login", json={"username": user, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    data = resp.json()
    token = data.get("token") or data.get("access_token") or data.get("sessionToken")
    assert token, "No token returned from login"
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=False)
def real_i3_guard(i3_mode: str) -> None:
    """Skip test if not running against a real i3."""
    if i3_mode != "real":
        pytest.skip("This test requires I3_TEST_MODE=real")
    _skip_if_real_mode_not_enabled()
