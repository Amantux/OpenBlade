"""Production configuration validation for OpenBlade.

Fails a deploy when production configuration is unsafe or incomplete: unsafe
default credentials/tokens, missing required settings, or development-only
settings left on in production. Importable (tested) and runnable as a CLI
(`scripts/validate_config.py`) so the same logic gates CI and pre-deployment.

This complements — does not replace — the existing runtime guards
(service_auth.py refuses the default token in production; the real-hardware gate).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

BLOCKING = "blocking"
WARNING = "warning"

# Kept in sync with openblade/api/service_auth.py::_DEFAULT_SERVICE_TOKEN
DEFAULT_SERVICE_TOKEN = "openblade-controller-dev-token-do-not-expose"
# Kept in sync with openblade/api/aml_state.py::_DEFAULT_ADMIN_PASSWORD / _DEFAULT_SERVICE_PASSWORD
DEFAULT_ADMIN_PASSWORD = "password"
DEFAULT_SERVICE_PASSWORD = "service123"
VALID_BACKENDS = {"mock", "simulator", "real"}


@dataclass(frozen=True)
class Finding:
    severity: str  # BLOCKING | WARNING
    code: str
    message: str


def validate_config(env: Mapping[str, str]) -> list[Finding]:
    """Return findings for the given environment. Deploy-blocking = any BLOCKING."""
    findings: list[Finding] = []
    is_prod = env.get("OPENBLADE_ENV", "development").strip().lower() == "production"

    backend = env.get("OPENBLADE_BACKEND", "mock").strip().lower()
    if backend not in VALID_BACKENDS:
        findings.append(
            Finding(
                WARNING,
                "backend_unrecognized",
                f"OPENBLADE_BACKEND={backend!r} is not one of {sorted(VALID_BACKENDS)}; runtime falls back to mock",
            )
        )

    if (
        backend == "real"
        and env.get("OPENBLADE_REAL_HARDWARE_ENABLED", "false").strip().lower() != "true"
    ):
        findings.append(
            Finding(
                WARNING,
                "real_hardware_gate_closed",
                "OPENBLADE_BACKEND=real but OPENBLADE_REAL_HARDWARE_ENABLED!=true; real hardware operations will be refused",
            )
        )

    if env.get(
        "OPENBLADE_ROBOTICS_TRANSPORT", "scsi"
    ).strip().lower() == "webservices" and not env.get("OPENBLADE_SCALAR_URL"):
        findings.append(
            Finding(
                BLOCKING if is_prod else WARNING,
                "scalar_url_missing",
                "OPENBLADE_ROBOTICS_TRANSPORT=webservices requires OPENBLADE_SCALAR_URL",
            )
        )

    # --- open gates: blocking in production, one loud banner line otherwise ---
    sev = BLOCKING if is_prod else WARNING
    if (
        not env.get("OPENBLADE_API_TOKEN", "").strip()
        and not env.get("OPENBLADE_API_TOKEN_FILE", "").strip()
    ):
        findings.append(
            Finding(
                sev,
                "native_api_open",
                "NATIVE API OPEN: OPENBLADE_API_TOKEN / OPENBLADE_API_TOKEN_FILE unset; "
                "every native mutating route is unauthenticated",
            )
        )
    origins = [o.strip() for o in env.get("OPENBLADE_CORS_ORIGINS", "").split(",")]
    if "*" in origins:
        findings.append(
            Finding(
                sev,
                "cors_wildcard",
                "CORS WILDCARD: OPENBLADE_CORS_ORIGINS contains '*' (credentials are allowed)",
            )
        )
    if not is_prod:
        if env.get("OPENBLADE_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD) == DEFAULT_ADMIN_PASSWORD:
            findings.append(
                Finding(
                    WARNING,
                    "default_admin_password",
                    "DEFAULT AML ADMIN PASSWORD: OPENBLADE_ADMIN_PASSWORD is unset or the default",
                )
            )
        if (
            env.get("OPENBLADE_SERVICE_PASSWORD", DEFAULT_SERVICE_PASSWORD)
            == DEFAULT_SERVICE_PASSWORD
        ):
            findings.append(
                Finding(
                    WARNING,
                    "default_service_password",
                    "DEFAULT AML SERVICE PASSWORD: OPENBLADE_SERVICE_PASSWORD is unset or the default",
                )
            )
        if env.get("OPENBLADE_SERVICE_TOKEN", DEFAULT_SERVICE_TOKEN) == DEFAULT_SERVICE_TOKEN:
            findings.append(
                Finding(
                    WARNING,
                    "default_service_token",
                    "DEFAULT SERVICE TOKEN: OPENBLADE_SERVICE_TOKEN is unset or the default",
                )
            )

    if not is_prod:
        return findings

    # --- production-only blocking checks ---
    admin = env.get("OPENBLADE_ADMIN_PASSWORD", "")
    if not admin or admin == DEFAULT_ADMIN_PASSWORD:
        findings.append(
            Finding(
                BLOCKING,
                "unsafe_admin_password",
                "OPENBLADE_ADMIN_PASSWORD is unset or the insecure default in production",
            )
        )

    svc_pw = env.get("OPENBLADE_SERVICE_PASSWORD", "")
    if not svc_pw or svc_pw == DEFAULT_SERVICE_PASSWORD:
        findings.append(
            Finding(
                BLOCKING,
                "missing_service_password",
                "OPENBLADE_SERVICE_PASSWORD is unset or the insecure default in production",
            )
        )

    token = env.get("OPENBLADE_SERVICE_TOKEN", "")
    if not token or token == DEFAULT_SERVICE_TOKEN:
        findings.append(
            Finding(
                BLOCKING,
                "unsafe_service_token",
                "OPENBLADE_SERVICE_TOKEN is unset or the insecure default in production",
            )
        )

    db_url = env.get("OPENBLADE_DB_URL", "")
    if not db_url:
        findings.append(
            Finding(BLOCKING, "missing_db_url", "OPENBLADE_DB_URL is unset in production")
        )
    elif "/tmp" in db_url or db_url.rstrip("/").endswith(".openblade/openblade.db"):
        findings.append(
            Finding(
                WARNING,
                "dev_db_path",
                f"OPENBLADE_DB_URL={db_url!r} looks like a development/default path",
            )
        )

    if env.get("OPENBLADE_LOG_LEVEL", "INFO").strip().upper() == "DEBUG":
        findings.append(
            Finding(
                WARNING, "debug_logging_in_production", "OPENBLADE_LOG_LEVEL=DEBUG in production"
            )
        )

    # emulator latency emulation should not be forced on for a real production controller
    if (
        env.get("OPENBLADE_EMULATOR_LATENCY_ENABLED", "").strip().lower() == "true"
        and backend == "real"
    ):
        findings.append(
            Finding(
                WARNING,
                "latency_emulation_in_production",
                "OPENBLADE_EMULATOR_LATENCY_ENABLED=true with a real backend injects artificial delay",
            )
        )

    return findings


def is_deployable(findings: list[Finding]) -> bool:
    return not any(f.severity == BLOCKING for f in findings)


# Codes the API process refuses to start on in production (startup gate). Other
# BLOCKING findings (e.g. missing_db_url) remain deploy-gate-only.
STARTUP_REFUSAL_CODES = frozenset(
    {
        "native_api_open",
        "cors_wildcard",
        "unsafe_admin_password",
        "missing_service_password",
        "unsafe_service_token",
    }
)


class ConfigurationError(RuntimeError):
    """Raised at startup when production configuration leaves an auth gate open.

    The message names variables/codes only; it never echoes configured values.
    """


def startup_banner(findings: list[Finding]) -> str:
    """One multi-line banner listing every open gate (empty string if none)."""
    if not findings:
        return ""
    bar = "!" * 72
    lines = [bar, "OPENBLADE SECURITY: the following gates are OPEN"]
    lines += [f"  - {f.message}" for f in findings]
    lines.append(bar)
    return "\n".join(lines)


def enforce_startup_config(env: Mapping[str, str]) -> list[Finding]:
    """Refuse to start in production with an open auth gate; else return findings.

    Collects every problem and reports them together. Outside production the
    caller logs :func:`startup_banner` of the returned findings.
    """
    findings = validate_config(env)
    refusals = [f for f in findings if f.severity == BLOCKING and f.code in STARTUP_REFUSAL_CODES]
    if refusals:
        raise ConfigurationError(
            "Refusing to start in OPENBLADE_ENV=production: "
            + "; ".join(f.message for f in refusals)
        )
    return findings
