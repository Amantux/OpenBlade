"""Tests for production configuration validation (operability gate).

Reproduces the "production starts with development settings" / "required env var
absent" failure classes and asserts they are blocked.
"""

from __future__ import annotations

from openblade.config_validation import (
    BLOCKING,
    DEFAULT_SERVICE_PASSWORD,
    DEFAULT_SERVICE_TOKEN,
    Finding,
    is_deployable,
    validate_config,
)


def _codes(findings: list[Finding], severity: str | None = None) -> set[str]:
    return {f.code for f in findings if severity is None or f.severity == severity}


def test_development_is_deployable_with_defaults() -> None:
    findings = validate_config({"OPENBLADE_ENV": "development"})
    assert is_deployable(findings)
    assert _codes(findings, BLOCKING) == set()


def test_production_blocks_unsafe_defaults() -> None:
    findings = validate_config({"OPENBLADE_ENV": "production"})
    assert not is_deployable(findings)
    assert {"unsafe_admin_password", "missing_service_password", "unsafe_service_token", "missing_db_url"} <= _codes(findings, BLOCKING)


def test_production_blocks_default_admin_password_value() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "password",  # the insecure default value
        "OPENBLADE_SERVICE_PASSWORD": "x",
        "OPENBLADE_SERVICE_TOKEN": "strong",
        "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
    })
    assert "unsafe_admin_password" in _codes(findings, BLOCKING)


def test_production_blocks_default_service_token() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "s3cret",
        "OPENBLADE_SERVICE_PASSWORD": "s3cret",
        "OPENBLADE_SERVICE_TOKEN": DEFAULT_SERVICE_TOKEN,
        "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
    })
    assert "unsafe_service_token" in _codes(findings, BLOCKING)


def test_production_blocks_default_service_password_value() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "s3cret",
        "OPENBLADE_SERVICE_PASSWORD": DEFAULT_SERVICE_PASSWORD,  # the shipped default 'service123'
        "OPENBLADE_SERVICE_TOKEN": "strong",
        "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
    })
    assert "missing_service_password" in _codes(findings, BLOCKING)
    assert not is_deployable(findings)


def test_production_deployable_when_secrets_set() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "s3cret-admin",
        "OPENBLADE_SERVICE_PASSWORD": "s3cret-svc",
        "OPENBLADE_SERVICE_TOKEN": "0a1b2c3d-strong-token",
        "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
    })
    assert is_deployable(findings), findings


def test_webservices_transport_requires_scalar_url_in_production() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "a", "OPENBLADE_SERVICE_PASSWORD": "b",
        "OPENBLADE_SERVICE_TOKEN": "t", "OPENBLADE_DB_URL": "sqlite:////data/db",
        "OPENBLADE_ROBOTICS_TRANSPORT": "webservices",  # no OPENBLADE_SCALAR_URL
    })
    assert "scalar_url_missing" in _codes(findings, BLOCKING)


def test_debug_logging_in_production_is_a_warning() -> None:
    findings = validate_config({
        "OPENBLADE_ENV": "production",
        "OPENBLADE_ADMIN_PASSWORD": "a", "OPENBLADE_SERVICE_PASSWORD": "b",
        "OPENBLADE_SERVICE_TOKEN": "t", "OPENBLADE_DB_URL": "sqlite:////data/db",
        "OPENBLADE_LOG_LEVEL": "DEBUG",
    })
    assert "debug_logging_in_production" in _codes(findings, "warning")
    assert is_deployable(findings)  # warning does not block
