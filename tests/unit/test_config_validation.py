"""Tests for production configuration validation (operability gate).

Reproduces the "production starts with development settings" / "required env var
absent" failure classes and asserts they are blocked.
"""

from __future__ import annotations

import pytest

from openblade.config_validation import (
    BLOCKING,
    DEFAULT_SERVICE_PASSWORD,
    DEFAULT_SERVICE_TOKEN,
    WARNING,
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
    assert {
        "unsafe_admin_password",
        "missing_service_password",
        "unsafe_service_token",
        "missing_db_url",
    } <= _codes(findings, BLOCKING)


def test_production_blocks_default_admin_password_value() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_ADMIN_PASSWORD": "password",  # the insecure default value
            "OPENBLADE_SERVICE_PASSWORD": "x-strong-service-pw",
            "OPENBLADE_SERVICE_TOKEN": "strong",
            "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
        }
    )
    assert "unsafe_admin_password" in _codes(findings, BLOCKING)


def test_production_blocks_default_service_token() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_ADMIN_PASSWORD": "s3cret-long-enough",
            "OPENBLADE_SERVICE_PASSWORD": "s3cret-long-enough",
            "OPENBLADE_SERVICE_TOKEN": DEFAULT_SERVICE_TOKEN,
            "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
        }
    )
    assert "unsafe_service_token" in _codes(findings, BLOCKING)


def test_production_blocks_default_service_password_value() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_ADMIN_PASSWORD": "s3cret-long-enough",
            "OPENBLADE_SERVICE_PASSWORD": DEFAULT_SERVICE_PASSWORD,  # the shipped default 'service123'
            "OPENBLADE_SERVICE_TOKEN": "strong",
            "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
        }
    )
    assert "missing_service_password" in _codes(findings, BLOCKING)
    assert not is_deployable(findings)


def test_production_deployable_when_secrets_set() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_API_TOKEN": "api",  # production requires native API auth
            "OPENBLADE_ADMIN_PASSWORD": "s3cret-admin-long",
            "OPENBLADE_SERVICE_PASSWORD": "s3cret-svc-long-pw",
            "OPENBLADE_SERVICE_TOKEN": "0a1b2c3d-strong-token",
            "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
        }
    )
    assert is_deployable(findings), findings


def test_webservices_transport_requires_scalar_url_in_production() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_ADMIN_PASSWORD": "a-strong-admin-pw",
            "OPENBLADE_SERVICE_PASSWORD": "b-strong-service-pw",
            "OPENBLADE_SERVICE_TOKEN": "t",
            "OPENBLADE_DB_URL": "sqlite:////data/db",
            "OPENBLADE_ROBOTICS_TRANSPORT": "webservices",  # no OPENBLADE_SCALAR_URL
        }
    )
    assert "scalar_url_missing" in _codes(findings, BLOCKING)


def test_debug_logging_in_production_is_a_warning() -> None:
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_API_TOKEN": "api",  # production requires native API auth
            "OPENBLADE_ADMIN_PASSWORD": "a-strong-admin-pw",
            "OPENBLADE_SERVICE_PASSWORD": "b-strong-service-pw",
            "OPENBLADE_SERVICE_TOKEN": "t",
            "OPENBLADE_DB_URL": "sqlite:////data/db",
            "OPENBLADE_LOG_LEVEL": "DEBUG",
        }
    )
    assert "debug_logging_in_production" in _codes(findings, "warning")
    assert is_deployable(findings)  # warning does not block


# --- fail-closed startup gate (WS-harden-auth) ---------------------------------

_SAFE_PROD = {
    "OPENBLADE_ENV": "production",
    "OPENBLADE_API_TOKEN": "api-strong",
    "OPENBLADE_ADMIN_PASSWORD": "s3cret-long-enough",
    "OPENBLADE_SERVICE_PASSWORD": "s3cret-long-enough",
    "OPENBLADE_SERVICE_TOKEN": "strong",
    "OPENBLADE_DB_URL": "sqlite:////data/openblade.db",
    "OPENBLADE_CORS_ORIGINS": "http://lan-host:8091",
}


def test_startup_safe_production_starts() -> None:
    from openblade.config_validation import enforce_startup_config

    enforce_startup_config(_SAFE_PROD)


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"OPENBLADE_API_TOKEN": ""}, "OPENBLADE_API_TOKEN"),
        ({"OPENBLADE_ADMIN_PASSWORD": "password"}, "OPENBLADE_ADMIN_PASSWORD"),
        ({"OPENBLADE_SERVICE_PASSWORD": "service123"}, "OPENBLADE_SERVICE_PASSWORD"),
        ({"OPENBLADE_SERVICE_TOKEN": DEFAULT_SERVICE_TOKEN}, "OPENBLADE_SERVICE_TOKEN"),
        ({"OPENBLADE_CORS_ORIGINS": "http://a,*"}, "OPENBLADE_CORS_ORIGINS"),
    ],
)
def test_startup_production_refuses_each_open_gate(override: dict[str, str], needle: str) -> None:
    from openblade.config_validation import ConfigurationError, enforce_startup_config

    with pytest.raises(ConfigurationError) as exc:
        enforce_startup_config({**_SAFE_PROD, **override})
    assert needle in str(exc.value)
    # Never echoes the configured secret values.
    assert "s3cret" not in str(exc.value) and "api-strong" not in str(exc.value)


def test_startup_api_token_file_satisfies_gate() -> None:
    from openblade.config_validation import enforce_startup_config

    enforce_startup_config(
        {**_SAFE_PROD, "OPENBLADE_API_TOKEN": "", "OPENBLADE_API_TOKEN_FILE": "/run/t"}
    )


def test_startup_development_banner_lists_every_open_gate() -> None:
    from openblade.config_validation import enforce_startup_config, startup_banner

    banner = startup_banner(
        enforce_startup_config({"OPENBLADE_ENV": "development", "OPENBLADE_CORS_ORIGINS": "*"})
    )
    for line in (
        "NATIVE API OPEN",
        "WEAK AML ADMIN PASSWORD",
        "WEAK AML SERVICE PASSWORD",
        "DEFAULT SERVICE TOKEN",
        "CORS WILDCARD",
    ):
        assert line in banner


@pytest.mark.parametrize(
    ("func", "var"),
    [
        ("_get_admin_password", "OPENBLADE_ADMIN_PASSWORD"),
        ("_get_service_password", "OPENBLADE_SERVICE_PASSWORD"),
    ],
)
def test_aml_default_password_refused_in_production(
    monkeypatch: pytest.MonkeyPatch, func: str, var: str
) -> None:
    from openblade.api import aml_state
    from openblade.config_validation import ConfigurationError

    monkeypatch.setenv("OPENBLADE_ENV", "production")
    monkeypatch.delenv(var, raising=False)
    with pytest.raises(ConfigurationError):
        getattr(aml_state, func)()
    monkeypatch.setenv("OPENBLADE_ENV", "development")
    assert getattr(aml_state, func)()  # dev keeps the warning-only behaviour


def test_api_startup_hook_refuses_production_without_api_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from openblade.api import main
    from openblade.config_validation import ConfigurationError

    for key, value in _SAFE_PROD.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OPENBLADE_API_TOKEN")
    monkeypatch.delenv("OPENBLADE_API_TOKEN_FILE", raising=False)
    with pytest.raises(ConfigurationError, match="OPENBLADE_API_TOKEN"):
        asyncio.run(main.initialize_aml_state())


def test_production_insecure_session_cookies_warn() -> None:
    findings = validate_config({**_SAFE_PROD, "OPENBLADE_WEB_SECURE_COOKIES": "false"})
    assert "insecure_session_cookies" in _codes(findings, WARNING)
    assert "insecure_session_cookies" not in _codes(validate_config(_SAFE_PROD))


def test_web_flask_refuses_production_without_secret_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openblade.web_flask import app as web_app

    monkeypatch.setenv("OPENBLADE_ENV", "production")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        web_app.create_app()


def test_web_flask_production_cookies_secure_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openblade.web_flask import app as web_app

    monkeypatch.setenv("OPENBLADE_ENV", "production")
    monkeypatch.setenv("FLASK_SECRET_KEY", "k" * 32)
    monkeypatch.delenv("OPENBLADE_WEB_SECURE_COOKIES", raising=False)
    assert web_app.create_app().config["SESSION_COOKIE_SECURE"] is True


def test_dev_db_path_finding_redacts_url_credentials() -> None:
    """WARNING findings are logged verbatim by the startup banner: no secrets."""
    findings = validate_config(
        {
            "OPENBLADE_ENV": "production",
            "OPENBLADE_DB_URL": "postgresql://ob:S3cretPassw0rd@db.lan:5432/tmp/openblade",
        }
    )
    msgs = [f.message for f in findings if f.code == "dev_db_path"]
    assert len(msgs) == 1
    assert "S3cretPassw0rd" not in msgs[0]
    assert "ob:" not in msgs[0]
    assert "postgresql://***@db.lan:5432/tmp/openblade" in msgs[0]
    assert all("S3cretPassw0rd" not in f.message for f in findings)


def test_redact_url_drops_query_and_handles_plain_paths() -> None:
    from openblade.config_validation import redact_url

    assert redact_url("postgresql://u:p@h/db?password=x") == "postgresql://***@h/db"
    assert redact_url("sqlite:////tmp/x.db") == "sqlite:////tmp/x.db"


@pytest.mark.parametrize(
    "value",
    [" ", "   \t", "short-pw", "password", "service123", "  service123  "],
    ids=["space", "whitespace", "short", "admin-default", "svc-default", "padded-default"],
)
@pytest.mark.parametrize(
    ("var", "code"),
    [
        ("OPENBLADE_ADMIN_PASSWORD", "unsafe_admin_password"),
        ("OPENBLADE_SERVICE_PASSWORD", "missing_service_password"),
    ],
)
def test_production_blocks_weak_passwords(value: str, var: str, code: str) -> None:
    env = dict(_SAFE_PROD)
    env[var] = value
    findings = validate_config(env)
    assert code in _codes(findings, BLOCKING)
    assert not is_deployable(findings)


@pytest.mark.parametrize("var", ["OPENBLADE_ADMIN_PASSWORD", "OPENBLADE_SERVICE_PASSWORD"])
def test_development_warns_on_short_password(var: str) -> None:
    findings = validate_config({"OPENBLADE_ENV": "development", var: "short-pw"})
    assert is_deployable(findings)
    msgs = [f.message for f in findings if f.severity == WARNING and var in f.message]
    assert msgs and "shorter than 12" in msgs[0] and "short-pw" not in msgs[0]


def test_twelve_char_password_is_accepted() -> None:
    from openblade.config_validation import password_problem

    assert password_problem("abcdefghijkl") is None
    assert password_problem("abcdefghijk") is not None


@pytest.mark.parametrize(
    ("getter", "var"),
    [
        ("_get_admin_password", "OPENBLADE_ADMIN_PASSWORD"),
        ("_get_service_password", "OPENBLADE_SERVICE_PASSWORD"),
    ],
)
@pytest.mark.parametrize("value", [" ", "short-pw", "password", "service123"])
def test_aml_password_getters_refuse_weak_values_in_production(
    monkeypatch: pytest.MonkeyPatch, getter: str, var: str, value: str
) -> None:
    from openblade.api import aml_state
    from openblade.config_validation import ConfigurationError

    monkeypatch.setenv("OPENBLADE_ENV", "production")
    monkeypatch.setenv(var, value)
    with pytest.raises(ConfigurationError) as exc:
        getattr(aml_state, getter)()
    assert value.strip() == "" or value not in str(exc.value)
    monkeypatch.setenv(var, "a-strong-password-1")
    assert getattr(aml_state, getter)() == "a-strong-password-1"
