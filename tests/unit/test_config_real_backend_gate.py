"""Real backend needs a third signal (API token); simulator alias; unknown warns."""

from __future__ import annotations

import logging

import pytest

from openblade.config import BackendMode, load_config
from openblade.config_validation import BLOCKING, validate_config
from openblade.domain.errors import RealHardwareDisabledError

REAL = {"OPENBLADE_BACKEND": "real", "OPENBLADE_REAL_HARDWARE_ENABLED": "true"}


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name in ("OPENBLADE_API_TOKEN", "OPENBLADE_API_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_load_config_real_enabled_without_api_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _env(monkeypatch, **REAL)
    with pytest.raises(RealHardwareDisabledError, match="OPENBLADE_API_TOKEN"):
        load_config()


def test_load_config_real_enabled_with_api_token_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, **REAL, OPENBLADE_API_TOKEN="t" * 32)
    assert load_config().backend == BackendMode.REAL


def test_validate_config_blocks_real_enabled_without_api_token() -> None:
    findings = validate_config(dict(REAL))
    assert any(
        f.code == "real_backend_requires_api_token" and f.severity == BLOCKING for f in findings
    )


def test_simulator_is_an_alias_of_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, OPENBLADE_BACKEND="simulator")
    assert load_config().backend == BackendMode.MOCK


def test_unknown_backend_warns_and_uses_mock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _env(monkeypatch, OPENBLADE_BACKEND="reall")
    with caplog.at_level(logging.WARNING, logger="openblade.config"):
        assert load_config().backend == BackendMode.MOCK
    assert "Unrecognised OPENBLADE_BACKEND" in caplog.text
