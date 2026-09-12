"""Coverage sweep for the native REST bearer-token layer.

The point of this module is that nobody has to remember anything. It walks
``app.openapi()`` and asserts that **every** OpenBlade-native operation answers
401 without a credential once ``OPENBLADE_API_TOKEN`` is set, so a route added
next month is covered the moment it appears in the schema.

It also pins the two things the sweep must NOT do: gate the Quantum AML
emulator surface (``/aml/*``, ``/iblade/*``, whose session auth is a wire
contract exercised by ``tests/i3``), and gate the health endpoints monitors
poll.

``test_sweep_catches_an_exempted_router`` is the mutation check: it simulates
"somebody left a router out of the chokepoint" and asserts the sweep goes red.
Without it, a sweep that silently found zero routes would pass forever.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api import api_auth
from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

TOKEN = "sweep-token-do-not-reuse"

_HTTP_METHODS = ("get", "post", "put", "delete", "patch")
_PATH_PARAM = re.compile(r"\{[^}]*\}")

# Paths that exist but are absent from the OpenAPI schema. They are part of the
# native surface and must be gated too.
_EXTRA_NATIVE_PATHS: tuple[tuple[str, str], ...] = (
    ("get", "/docs"),
    ("get", "/redoc"),
    ("get", "/openapi.json"),
)


# The policy, restated independently of the implementation. Deriving the route
# list from ``api_auth.requires_api_token`` would make this sweep vacuous:
# exempting a router would also delete it from the sweep, and the tests would
# stay green. (Verified — that exact mutation passed until this was rewritten.)
_AML_SURFACE_PREFIXES = ("/aml", "/iblade")
_EXPECTED_OPEN_PATHS = frozenset({"/health", "/healthz", "/readyz"})


def _is_aml_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _AML_SURFACE_PREFIXES)


def _must_be_gated(path: str) -> bool:
    return not _is_aml_path(path) and (path.rstrip("/") or "/") not in _EXPECTED_OPEN_PATHS


def _native_operations() -> list[tuple[str, str]]:
    """Every (method, path) in the OpenAPI schema that the token must gate."""
    schema = app.openapi()
    operations = {
        (method, path)
        for path, item in schema.get("paths", {}).items()
        if _must_be_gated(path)
        for method in _HTTP_METHODS
        if method in item
    }
    return sorted(operations | set(_EXTRA_NATIVE_PATHS))


def _aml_operations() -> list[tuple[str, str]]:
    schema = app.openapi()
    return sorted(
        {
            (method, path)
            for path, item in schema.get("paths", {}).items()
            if _is_aml_path(path)
            for method in _HTTP_METHODS
            if method in item
        }
    )


NATIVE_OPERATIONS = _native_operations()
AML_OPERATIONS = _aml_operations()


def _concrete(path: str) -> str:
    """Fill path parameters so the URL routes; the 401 precedes any handler."""
    return _PATH_PARAM.sub("1", path)


@pytest.fixture()
def auth_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(api_auth.API_TOKEN_ENV_VAR, TOKEN)
    monkeypatch.delenv(api_auth.API_TOKEN_FILE_ENV_VAR, raising=False)
    api_auth.reset_api_auth_cache()
    yield
    api_auth.reset_api_auth_cache()


@pytest.fixture()
def auth_disabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(api_auth.API_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(api_auth.API_TOKEN_FILE_ENV_VAR, raising=False)
    api_auth.reset_api_auth_cache()
    yield
    api_auth.reset_api_auth_cache()


@pytest.fixture(scope="module", autouse=True)
def _app_context(tmp_path_factory: pytest.TempPathFactory) -> None:
    # Module-scoped: the sweep issues ~400 requests and rebuilding the app
    # context (and its SQLite schema) per parameter turns a 15-second run into
    # ten minutes. Nothing here mutates catalog state -- the auth-enabled cases
    # 401 before routing ever happens, and the authenticated cases are reads.
    db_path = tmp_path_factory.mktemp("auth-sweep") / "auth-sweep.db"
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{db_path}")))


@pytest.fixture()
def client() -> TestClient:
    # Function-scoped so AML session cookies never leak between tests (which
    # would make an "is this route still gated?" assertion silently pass).
    return TestClient(app)


def _unprotected(client: TestClient) -> list[str]:
    """Native operations that answered something other than 401 without a token."""
    leaks: list[str] = []
    for method, path in NATIVE_OPERATIONS:
        response = client.request(method.upper(), _concrete(path))
        if response.status_code != 401:
            leaks.append(f"{method.upper()} {path} -> {response.status_code}")
    return leaks


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------
def test_the_sweep_actually_found_routes() -> None:
    # Anti-vacuity: an empty parametrization would make every assertion below
    # pass without testing anything.
    assert len(NATIVE_OPERATIONS) > 50
    assert len(AML_OPERATIONS) > 50
    # The two routes named in the task that motivated this layer.
    assert ("get", "/jobs/") in NATIVE_OPERATIONS
    assert ("post", "/ltfs/format") in NATIVE_OPERATIONS
    # ... and the one that can run arbitrary test commands.
    assert ("post", "/api/test-runner/run") in NATIVE_OPERATIONS


@pytest.mark.parametrize(
    ("method", "path"),
    NATIVE_OPERATIONS,
    ids=[f"{method.upper()} {path}" for method, path in NATIVE_OPERATIONS],
)
def test_every_native_route_401s_without_a_token(
    auth_enabled: None, client: TestClient, method: str, path: str
) -> None:
    response = client.request(method.upper(), _concrete(path))
    assert response.status_code == 401, (
        f"{method.upper()} {path} returned {response.status_code}; it is not covered "
        "by the api_auth chokepoint"
    )


def test_sweep_catches_an_exempted_router(
    auth_enabled: None, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation check: exempt a router from the chokepoint, sweep must go red."""
    assert _unprotected(client) == []

    original = api_auth.requires_api_token

    def leaky(path: str) -> bool:
        # Simulates the /jobs router being left out of the auth layer.
        if path == "/jobs" or path.startswith("/jobs/"):
            return False
        return original(path)

    monkeypatch.setattr(api_auth, "requires_api_token", leaky)
    leaks = _unprotected(client)
    assert leaks, "the sweep did not notice an unprotected router — it is vacuous"
    assert any(leak.startswith("GET /jobs") for leak in leaks)


def test_the_sweep_does_not_take_its_route_list_from_the_code_it_tests() -> None:
    """The sweep must restate the policy, not mirror the implementation.

    If ``NATIVE_OPERATIONS`` were built from ``api_auth.requires_api_token``,
    widening an exemption would delete routes from the sweep instead of failing
    it. Today the two agree; this pins that they are computed independently.
    """
    from_implementation = {
        (method, path) for method, path in NATIVE_OPERATIONS if api_auth.requires_api_token(path)
    }
    assert from_implementation == set(NATIVE_OPERATIONS), (
        "api_auth exempts a path the sweep expects to be gated: "
        f"{sorted(set(NATIVE_OPERATIONS) - from_implementation)}"
    )


# ---------------------------------------------------------------------------
# Scope: what the token must NOT gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/health", "/healthz", "/readyz"])
def test_health_is_open_with_auth_enabled(
    auth_enabled: None, client: TestClient, path: str
) -> None:
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/health", "/healthz", "/readyz"])
def test_health_is_open_with_auth_disabled(
    auth_disabled: None, client: TestClient, path: str
) -> None:
    assert client.get(path).status_code == 200


def test_aml_surface_is_untouched_when_auth_is_enabled(
    auth_enabled: None, client: TestClient
) -> None:
    # The AML session layer, not the API token, decides these. A protected AML
    # route answers with the AML 401 shape, never the native one.
    response = client.get("/aml/users")
    assert response.status_code == 401
    assert response.json().get("code") == "AML_AUTH_REQUIRED"

    assert client.get("/aml/users/ldap/enabled").status_code == 200
    assert (
        client.post("/aml/users/login", json={"name": "admin", "password": "password"}).status_code
        == 200
    )
    # An AML session alone -- no API token -- still reaches the AML surface.
    assert client.get("/aml/users").status_code == 200


def test_aml_surface_is_untouched_when_auth_is_disabled(
    auth_disabled: None, client: TestClient
) -> None:
    assert client.get("/aml/users/ldap/enabled").status_code == 200
    assert (
        client.post("/aml/users/login", json={"name": "admin", "password": "password"}).status_code
        == 200
    )


def test_an_aml_session_token_does_not_unlock_the_native_surface(
    auth_enabled: None, client: TestClient
) -> None:
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert login.status_code == 200
    session_id = client.cookies.get("sessionID")
    assert session_id
    response = client.get("/jobs", headers={"Authorization": f"Bearer {session_id}"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Behaviour with a credential / with auth off
# ---------------------------------------------------------------------------
def test_valid_token_reaches_the_route(auth_enabled: None, client: TestClient) -> None:
    response = client.get("/jobs", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200


def test_wrong_token_is_rejected(auth_enabled: None, client: TestClient) -> None:
    response = client.get("/jobs", headers={"Authorization": f"Bearer {TOKEN}x"})
    assert response.status_code == 401


def test_non_bearer_scheme_is_rejected(auth_enabled: None, client: TestClient) -> None:
    assert client.get("/jobs", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert client.get("/jobs", headers={"Authorization": TOKEN}).status_code == 401


def test_disabled_mode_leaves_the_native_surface_open(
    auth_disabled: None, client: TestClient
) -> None:
    assert client.get("/jobs").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_are_protected_only_when_auth_is_enabled(
    auth_enabled: None, client: TestClient
) -> None:
    assert client.get("/docs").status_code == 401
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


# ---------------------------------------------------------------------------
# The 401 itself
# ---------------------------------------------------------------------------
def test_401_body_is_curated_and_never_echoes_the_supplied_token(
    auth_enabled: None, client: TestClient
) -> None:
    supplied = "guessed-token-abc123"
    response = client.get("/jobs", headers={"Authorization": f"Bearer {supplied}"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    body = response.text
    assert supplied not in body
    assert TOKEN not in body
    assert response.json() == {
        "error": api_auth.UNAUTHORIZED_BODY["error"],
        "detail": api_auth.UNAUTHORIZED_BODY["detail"],
    }


def test_failure_log_scrubs_crlf_from_client_supplied_values(
    auth_enabled: None, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="openblade.api.api_auth"):
        client.get("/jobs", headers={"X-Forwarded-For": "1.2.3.4\r\nFAKE: injected"})
    records = [r for r in caplog.records if "Rejected unauthenticated" in r.getMessage()]
    assert records
    message = records[-1].getMessage()
    assert "\r" not in message
    assert "\n" not in message


def test_token_file_is_honoured_end_to_end(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "api-token"
    token_path.write_text("file-sourced-token\n", encoding="utf-8")
    token_path.chmod(0o600)
    monkeypatch.setenv(api_auth.API_TOKEN_FILE_ENV_VAR, str(token_path))
    monkeypatch.setenv(api_auth.API_TOKEN_ENV_VAR, "ignored-because-file-wins")
    api_auth.reset_api_auth_cache()
    try:
        assert client.get("/jobs").status_code == 401
        assert (
            client.get(
                "/jobs", headers={"Authorization": "Bearer ignored-because-file-wins"}
            ).status_code
            == 401
        )
        assert (
            client.get("/jobs", headers={"Authorization": "Bearer file-sourced-token"}).status_code
            == 200
        )
    finally:
        api_auth.reset_api_auth_cache()


def test_cors_preflight_is_not_gated(auth_enabled: None, client: TestClient) -> None:
    response = client.options(
        "/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code != 401
