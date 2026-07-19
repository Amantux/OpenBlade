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
        findings.append(Finding(WARNING, "backend_unrecognized",
            f"OPENBLADE_BACKEND={backend!r} is not one of {sorted(VALID_BACKENDS)}; runtime falls back to mock"))

    if backend == "real" and env.get("OPENBLADE_REAL_HARDWARE_ENABLED", "false").strip().lower() != "true":
        findings.append(Finding(WARNING, "real_hardware_gate_closed",
            "OPENBLADE_BACKEND=real but OPENBLADE_REAL_HARDWARE_ENABLED!=true; real hardware operations will be refused"))

    if env.get("OPENBLADE_ROBOTICS_TRANSPORT", "scsi").strip().lower() == "webservices" and not env.get("OPENBLADE_SCALAR_URL"):
        findings.append(Finding(BLOCKING if is_prod else WARNING, "scalar_url_missing",
            "OPENBLADE_ROBOTICS_TRANSPORT=webservices requires OPENBLADE_SCALAR_URL"))

    if not is_prod:
        return findings

    # --- production-only blocking checks ---
    admin = env.get("OPENBLADE_ADMIN_PASSWORD", "")
    if not admin or admin == DEFAULT_ADMIN_PASSWORD:
        findings.append(Finding(BLOCKING, "unsafe_admin_password",
            "OPENBLADE_ADMIN_PASSWORD is unset or the insecure default in production"))

    svc_pw = env.get("OPENBLADE_SERVICE_PASSWORD", "")
    if not svc_pw or svc_pw == DEFAULT_SERVICE_PASSWORD:
        findings.append(Finding(BLOCKING, "missing_service_password",
            "OPENBLADE_SERVICE_PASSWORD is unset or the insecure default in production"))

    token = env.get("OPENBLADE_SERVICE_TOKEN", "")
    if not token or token == DEFAULT_SERVICE_TOKEN:
        findings.append(Finding(BLOCKING, "unsafe_service_token",
            "OPENBLADE_SERVICE_TOKEN is unset or the insecure default in production"))

    db_url = env.get("OPENBLADE_DB_URL", "")
    if not db_url:
        findings.append(Finding(BLOCKING, "missing_db_url", "OPENBLADE_DB_URL is unset in production"))
    elif "/tmp" in db_url or db_url.rstrip("/").endswith(".openblade/openblade.db"):
        findings.append(Finding(WARNING, "dev_db_path",
            f"OPENBLADE_DB_URL={db_url!r} looks like a development/default path"))

    if env.get("OPENBLADE_LOG_LEVEL", "INFO").strip().upper() == "DEBUG":
        findings.append(Finding(WARNING, "debug_logging_in_production",
            "OPENBLADE_LOG_LEVEL=DEBUG in production"))

    # emulator latency emulation should not be forced on for a real production controller
    if env.get("OPENBLADE_EMULATOR_LATENCY_ENABLED", "").strip().lower() == "true" and backend == "real":
        findings.append(Finding(WARNING, "latency_emulation_in_production",
            "OPENBLADE_EMULATOR_LATENCY_ENABLED=true with a real backend injects artificial delay"))

    return findings


def is_deployable(findings: list[Finding]) -> bool:
    return not any(f.severity == BLOCKING for f in findings)
