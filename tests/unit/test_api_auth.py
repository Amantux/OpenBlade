"""Unit tests for the native REST bearer-token layer (openblade.api.api_auth)."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from openblade.api import api_auth
from openblade.api.api_auth import (
    API_TOKEN_ENV_VAR,
    API_TOKEN_FILE_ENV_VAR,
    ApiAuthConfigError,
    extract_bearer_token,
    is_aml_surface_path,
    is_open_path,
    requires_api_token,
    resolve_api_token,
    strip_forwarded_prefix,
    tokens_match,
    unauthorized_response,
)


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    api_auth.reset_api_auth_cache()
    yield
    api_auth.reset_api_auth_cache()


def _token_file(tmp_path: Path, contents: str, *, mode: int = 0o600) -> Path:
    path = tmp_path / "api-token"
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return path


# ---------------------------------------------------------------------------
# Token resolution + precedence
# ---------------------------------------------------------------------------
def test_unset_environment_disables_auth() -> None:
    resolution = resolve_api_token({})
    assert resolution.token is None
    assert resolution.enabled is False
    assert resolution.source == "unset"


def test_empty_string_counts_as_unset() -> None:
    resolution = resolve_api_token({API_TOKEN_ENV_VAR: "   "})
    assert resolution.enabled is False


def test_env_var_enables_auth() -> None:
    resolution = resolve_api_token({API_TOKEN_ENV_VAR: "swordfish"})
    assert resolution.token == "swordfish"
    assert resolution.source == "env"
    assert resolution.warnings == ()


def test_token_file_is_read_and_stripped(tmp_path: Path) -> None:
    path = _token_file(tmp_path, "file-token\n")
    resolution = resolve_api_token({API_TOKEN_FILE_ENV_VAR: str(path)})
    assert resolution.token == "file-token"
    assert resolution.source == "file"
    assert resolution.path == str(path)


def test_token_file_wins_over_env_var(tmp_path: Path) -> None:
    path = _token_file(tmp_path, "from-file")
    resolution = resolve_api_token(
        {API_TOKEN_FILE_ENV_VAR: str(path), API_TOKEN_ENV_VAR: "from-env"}
    )
    assert resolution.token == "from-file"
    assert resolution.source == "file"
    assert any("file wins" in warning for warning in resolution.warnings)


def test_loose_token_file_permissions_warn(tmp_path: Path) -> None:
    path = _token_file(tmp_path, "from-file", mode=0o644)
    resolution = resolve_api_token({API_TOKEN_FILE_ENV_VAR: str(path)})
    assert resolution.token == "from-file"
    assert any("0600" in warning for warning in resolution.warnings)


def test_tight_token_file_permissions_do_not_warn(tmp_path: Path) -> None:
    path = _token_file(tmp_path, "from-file", mode=0o600)
    resolution = resolve_api_token({API_TOKEN_FILE_ENV_VAR: str(path)})
    assert resolution.warnings == ()


def test_missing_token_file_fails_closed(tmp_path: Path) -> None:
    # An operator who asked for auth must never silently get none.
    with pytest.raises(ApiAuthConfigError):
        resolve_api_token({API_TOKEN_FILE_ENV_VAR: str(tmp_path / "nope")})


def test_empty_token_file_fails_closed(tmp_path: Path) -> None:
    path = _token_file(tmp_path, "\n  \n")
    with pytest.raises(ApiAuthConfigError):
        resolve_api_token({API_TOKEN_FILE_ENV_VAR: str(path)})


def test_resolution_defaults_to_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV_VAR, "ambient")
    monkeypatch.delenv(API_TOKEN_FILE_ENV_VAR, raising=False)
    assert api_auth.get_api_token_resolution().token == "ambient"


def test_resolution_is_cached_until_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV_VAR, "first")
    monkeypatch.delenv(API_TOKEN_FILE_ENV_VAR, raising=False)
    assert api_auth.get_api_token_resolution().token == "first"
    monkeypatch.setenv(API_TOKEN_ENV_VAR, "second")
    assert api_auth.get_api_token_resolution().token == "first"
    api_auth.reset_api_auth_cache()
    assert api_auth.get_api_token_resolution().token == "second"


# ---------------------------------------------------------------------------
# Startup warning
# ---------------------------------------------------------------------------
def test_disabled_auth_logs_a_loud_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(API_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(API_TOKEN_FILE_ENV_VAR, raising=False)
    with caplog.at_level(logging.WARNING, logger="openblade.api.api_auth"):
        api_auth.log_api_auth_status()
    assert any(
        record.levelno >= logging.WARNING and "AUTHENTICATION IS DISABLED" in record.getMessage()
        for record in caplog.records
    )


def test_enabled_auth_does_not_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV_VAR, "swordfish")
    monkeypatch.delenv(API_TOKEN_FILE_ENV_VAR, raising=False)
    with caplog.at_level(logging.INFO, logger="openblade.api.api_auth"):
        api_auth.log_api_auth_status()
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_startup_log_never_contains_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV_VAR, "super-secret-value")
    monkeypatch.delenv(API_TOKEN_FILE_ENV_VAR, raising=False)
    with caplog.at_level(logging.DEBUG, logger="openblade.api.api_auth"):
        api_auth.log_api_auth_status()
    assert "super-secret-value" not in caplog.text


# ---------------------------------------------------------------------------
# Header parsing + comparison
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER   abc  ", "abc"),
        ("Basic abc", None),
        ("abc", None),
        ("Bearer", None),
        ("Bearer    ", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_bearer_token(header: str | None, expected: str | None) -> None:
    assert extract_bearer_token(header) == expected


def test_tokens_match_is_constant_time() -> None:
    assert tokens_match("abc", "abc") is True
    assert tokens_match("abc", "abd") is False
    assert tokens_match("abc", "abcd") is False
    # Non-ASCII must compare rather than raise (compare_digest rejects non-ASCII str).
    assert tokens_match("pässwörd", "pässwörd") is True


def test_source_uses_secrets_compare_digest() -> None:
    source = inspect.getsource(api_auth)
    assert "secrets.compare_digest" in source
    assert "secrets.compare_digest" in inspect.getsource(api_auth.tokens_match)
    # Nothing in the module may compare credentials with ==.
    assert "supplied == " not in source


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    ["/aml", "/aml/system", "/aml/devices/blade/ltfs/1/status", "/iblade", "/iblade/sections"],
)
def test_aml_surface_is_never_gated(path: str) -> None:
    assert is_aml_surface_path(path) is True
    assert requires_api_token(path) is False


@pytest.mark.parametrize("path", ["/amlfoo", "/ibladex", "/", "/jobs", "/api/libraries"])
def test_lookalike_paths_are_native(path: str) -> None:
    assert is_aml_surface_path(path) is False


@pytest.mark.parametrize("path", ["/health", "/healthz", "/readyz", "/health/"])
def test_health_endpoints_stay_open(path: str) -> None:
    assert is_open_path(path) is True
    assert requires_api_token(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "/jobs",
        "/ltfs/format",
        "/api/test-runner/run",
        "/docs",
        "/openapi.json",
        "/version",
        "/status/library",
    ],
)
def test_native_paths_require_a_token(path: str) -> None:
    assert requires_api_token(path) is True


@pytest.mark.parametrize(
    ("path", "prefix", "expected"),
    [
        ("/proxy/aml/system", "/proxy", "/aml/system"),
        ("/proxy/aml/system", "/proxy/", "/aml/system"),
        ("/proxy", "/proxy", "/"),
        ("/proxyfoo/aml", "/proxy", "/proxyfoo/aml"),
        ("/aml/system", "", "/aml/system"),
        ("/aml/system", "not-a-path", "/aml/system"),
    ],
)
def test_strip_forwarded_prefix(path: str, prefix: str, expected: str) -> None:
    assert strip_forwarded_prefix(path, prefix) == expected


# ---------------------------------------------------------------------------
# Response body
# ---------------------------------------------------------------------------
def test_unauthorized_response_is_curated_and_echoes_nothing() -> None:
    response = unauthorized_response()
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    body = response.body.decode("utf-8")
    assert "Unauthorized" in body
    assert API_TOKEN_ENV_VAR in body


def test_scrub_removes_crlf_and_control_characters() -> None:
    scrubbed = api_auth._scrub("1.2.3.4\r\nX-Injected: yes\tvalue")
    assert "\r" not in scrubbed
    assert "\n" not in scrubbed
    assert "\t" not in scrubbed


def test_scrub_is_length_bounded() -> None:
    assert len(api_auth._scrub("a" * 10_000)) <= 200


def test_open_paths_are_a_small_set() -> None:
    # A guard against the exemption list quietly growing into a hole.
    assert set(api_auth.OPEN_PATHS) == {"/health", "/healthz", "/readyz"}


def test_env_var_names_are_stable() -> None:
    # These are documented in docs/wiki/guides/api-authentication.md and baked
    # into operator deployments; renaming them is a breaking change.
    assert API_TOKEN_ENV_VAR == "OPENBLADE_API_TOKEN"
    assert API_TOKEN_FILE_ENV_VAR == "OPENBLADE_API_TOKEN_FILE"
