---
title: Component — api
document_type: component
component: api
status: verified
last_verified: 2026-07-19
verified_against: [openblade/api/main.py, Dockerfile, openblade/bootstrap.py]
owners: [platform]
tags: [component, api]
---

# Component: `api`

The FastAPI control plane **and** i3 emulator (dual identity, see [system overview](../system-overview.md)).

| Property | Value |
|---|---|
| Runtime | Python 3.12, FastAPI, uvicorn |
| Entry / start | `uvicorn openblade.api.main:app --host 0.0.0.0 --port 8000` |
| Port | 8000 |
| Data store | SQLite catalog (`OPENBLADE_DB_URL`) + in-memory `aml_state` |
| Liveness | `GET /healthz` | 
| Readiness | `GET /readyz` |
| Metrics | `GET /aml/system/emulator/latency/metrics/prometheus` (auth) |
| Logs | structlog → stdout (default config) |
| Shutdown | uvicorn SIGTERM; in-memory `aml_state` lost (expected) |
| Restart | catalog persists; emulator/audit/metrics reset |
| Tests | `tests/unit` (44), `tests/integration` (35), `tests/i3` (22) |
| Owner | platform |

## Dependencies
Catalog (SQLite), the active [backend](backends.md), emulator fleet (HTTP, optional),
assistant LLM endpoint (optional).

## Common failure modes
[API-001](../operations/failure-taxonomy.yaml) not starting, [HEALTH-001](../operations/failure-taxonomy.yaml) readiness, [API-002](../operations/failure-taxonomy.yaml) latency. Runbooks: [RB-API-001](../runbooks/service-not-starting.md), [RB-HEALTH-001](../runbooks/readiness-failing.md).

## Scaling constraints
Single in-process job worker; in-memory `aml_state` is per-process (not shared across replicas) — horizontal scaling of the emulator role is non-trivial.
