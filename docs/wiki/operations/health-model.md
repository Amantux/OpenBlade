---
title: Health Model
document_type: operations
status: verified
last_verified: 2026-07-19
verified_against:
  - openblade/api/routes_health.py:45
  - openblade/nas/health_service.py:25
  - openblade/nas/health_service.py:45
  - openblade/nas/health_service.py:61
owners: [platform]
tags: [health, operations, agent]
---

# Health Model

## Endpoints (`verified`)

| Endpoint | Auth | Checks | Semantics |
|---|---|---|---|
| `GET /healthz` | none | database + library + ltfs components | **always HTTP 200**; status is in the body |
| `GET /readyz` | none | database + library (NOT ltfs) | always 200; `ready` boolean + `reason` in body |
| `GET /version` | none | — | version string |

**Important for the agent:** health is in the JSON body, not the HTTP status code.
Do not treat HTTP 200 as "healthy" — parse `status` / `ready`.

## Component states (`verified` — `openblade/nas/health_service.py`)

Per-component status ∈ `ok · degraded · unhealthy`; overall = worst component
(`_STATUS_PRIORITY`).

| Component | ok | degraded | unhealthy |
|---|---|---|---|
| database | all probes pass (`datasets`, `path_mappings`, `cartridges`, `rebuild_runs`) | some probes fail | all probes fail |
| library | connected, drives > 0 | connected, 0 drives | exception |
| ltfs | reachable | not fully configured | exception |

## Readiness rule (`verified`)

`ready=false` when database status ≠ `ok` (reason `"database unavailable"`) OR
library status ≠ `ok` (reason `"library unavailable"`); on exception,
`reason="dependency check unavailable"`.

> **Verified quirk:** a *connected library with 0 drives* is `degraded` → **readiness
> fails**. A readiness failure is therefore NOT proof of a DB outage. Always read the
> `/healthz` component breakdown before diagnosing (see [confidence policy](../agent/confidence-policy.md)).

## Wiki-level health states (for agent classification)

Extends the app's states with operational meaning:

| State | Evidence required | Affected capability | Permitted agent action | Notify |
|---|---|---|---|---|
| `healthy` | `/readyz ready=true`, `/healthz` all `ok` | full | diagnostics only | no |
| `degraded` | any component `degraded`; core path still works | reduced | diagnostics + approved remediation | grouped |
| `unhealthy` | any component `unhealthy` | archive/restore impaired | diagnostics + escalate | immediate (high) |
| `unavailable` | `/healthz` unreachable | total | diagnostics (restart approval-gated, dev only) | page (critical) |
| `unknown` | telemetry missing (e.g. metrics endpoint auth-gated / down) | undetermined | notify; do not remediate | yes |
