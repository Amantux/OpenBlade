---
title: Secrets and the Agent Permission Model
document_type: configuration
status: verified
last_verified: 2026-07-19
verified_against: [openblade/api/service_auth.py, openblade/assistant/config.py, docker-compose.yml, .github/workflows/hardware-library-smoke.yml]
owners: [platform, security]
tags: [secrets, security, agent]
---

# Secrets and Agent Permissions

## Secrets in the system (verified)
- Admin/service passwords (`OPENBLADE_ADMIN_PASSWORD`, `OPENBLADE_SERVICE_PASSWORD`; defaults warn in logs — rotate before production).
- Service token `OPENBLADE_SERVICE_TOKEN` (`service_auth.py`; default refused in production).
- Assistant `OPENBLADE_ASSISTANT_API_KEY` / `OPENAI_API_KEY`.
- CI secrets `QUANTUM_AML_USER/PASSWORD`, `OPENBLADE_ADMIN_USER/PASSWORD` (hardware smoke).

## Rules
- **Never log or commit** secrets. Do not put them in audit records (redact).
- The Prometheus scrape endpoint is auth-gated — its scrape credential is itself a secret.

## Agent least-privilege capabilities (`proposed`)
Distinct, minimal grants — never one super-credential:

| Capability | Grants | Denies |
|---|---|---|
| `telemetry_read` | health/metrics/logs read | writes |
| `synthetic_probe` | one probe login | real user creds |
| `container_restart_dev` | restart 1 dev/emulator container | prod, stateful |
| `process_restart_dev` | restart api in dev | prod |

No secret-read, DB-write, deploy, infra, or `real`-env capability is granted to the agent.
Kill switch: `OPENBLADE_AGENT_WRITE_ENABLED` (proposed) disables writes, keeps diagnostics.
