"""
Shared fixtures for the Quantum i3 test suite.

Env vars:
    I3_TEST_MODE              emulator (default) | real
    I3_AML_URL                base URL for AML API (default: http://localhost:8000)
    I3_AML_USER               AML username (default: admin)
    I3_AML_PASSWORD           AML password (default: password)
    I3_TIMING_PROFILE         instant | realistic | hardware
    I3_REAL_HARDWARE_ENABLED  safety gate — must be "true" to run real-i3 tests
    OPENBLADE_API_TOKEN       native-REST bearer token, if the target enforces one
    OPENBLADE_API_TOKEN_FILE  file holding that token (takes precedence)
"""
from __future__ import annotations

import os
from collections.abc import Generator

import httpx
import pytest

from openblade.api.api_auth import get_api_token_resolution, is_aml_surface_path
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
    if (
        os.environ.get("I3_TEST_MODE", "emulator") == "real"
        and os.environ.get("I3_REAL_HARDWARE_ENABLED", "false").lower() != "true"
    ):
        pytest.skip(
            "Real i3 mode requires I3_REAL_HARDWARE_ENABLED=true. "
            "Set both I3_TEST_MODE=real and I3_REAL_HARDWARE_ENABLED=true to proceed."
        )


def _native_api_token() -> str | None:
    """The bearer token the target enforces on its OpenBlade-native surface.

    ``None`` when the target runs with native auth disabled, which is the
    default and what every existing workflow does.

    Uses the cached accessor, not ``resolve_api_token()``: the latter stats and
    re-reads the token file on every single request, and a transient read error
    would raise ``ApiAuthConfigError`` from inside an httpx event hook -- turning
    a config problem into a confusing traceback on every test in the suite.
    """
    return get_api_token_resolution().token


def _attach_native_api_token(request: httpx.Request) -> None:
    """Authenticate native-surface requests, leaving the AML surface alone.

    The i3 suite drives both surfaces through one client:

    * ``/aml/*`` and ``/iblade/*`` authenticate with the AML session credential
      the ``auth_headers`` fixture produces, so their Authorization header must
      survive untouched.
    * Everything else is OpenBlade-native and needs the API token in
      Authorization instead.

    A few native routes (``/api/libraries``, ``/status/*``) want *both*: the API
    token at the edge and an AML session at the route. Two credentials, one
    header -- so the AML session moves to the ``sessionID`` cookie, which is the
    channel ``routes_aml_auth.require_auth`` prefers anyway.

    A no-op when the target runs with native auth disabled, which is the default
    and what every existing workflow does.
    """
    token = _native_api_token()
    if token is None or is_aml_surface_path(request.url.path):
        return

    existing = request.headers.get("Authorization", "")
    scheme, _, value = existing.partition(" ")
    value = value.strip()
    if value and value != token:
        if scheme.lower() == "bearer":
            # An AML session id: move it to the cookie so it survives.
            cookie = request.headers.get("Cookie", "")
            if "sessionID=" not in cookie:
                session = f"sessionID={value}"
                request.headers["Cookie"] = f"{cookie}; {session}" if cookie else session
        else:
            # auth_headers falls back to Basic when /aml/auth/login is
            # unavailable. There is nowhere to put a Basic credential once the
            # API token claims Authorization, so say so rather than overwriting
            # it and leaving a bare 401 to debug.
            raise RuntimeError(
                f"Cannot carry a {scheme or 'non-bearer'} AML credential alongside the "
                "native API token: both want the Authorization header. The AML login "
                "endpoint is presumably down -- fix that, or run the suite without "
                "OPENBLADE_API_TOKEN set."
            )
    request.headers["Authorization"] = f"Bearer {token}"


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
    with httpx.Client(
        base_url=i3_base_url,
        timeout=120.0,
        event_hooks={"request": [_attach_native_api_token]},
    ) as client:
        yield client


@pytest.fixture
def auth_headers(i3_client: httpx.Client, i3_credentials: tuple[str, str]) -> dict[str, str]:
    """Return Authorization headers after logging in.

    Uses the AML /aml/auth/login endpoint. Returns Bearer token headers.
    Falls back to Basic auth if login endpoint is unavailable.
    """
    user, password = i3_credentials
    try:
        # Use a transient client to avoid mutating the shared i3_client cookies
        with httpx.Client(base_url=i3_client.base_url, timeout=30.0) as tmp:
            resp = tmp.post("/aml/auth/login", json={"username": user, "password": password})
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
    # Use a transient client to avoid mutating the shared i3_client cookies
    with httpx.Client(base_url=i3_client.base_url, timeout=30.0) as tmp:
        resp = tmp.post("/aml/auth/login", json={"username": user, "password": password})
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
