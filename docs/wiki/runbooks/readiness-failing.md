---
title: Readiness failing (/readyz)
document_type: runbook
component: api
services: [api]
failure_ids: [HEALTH-001]
symptoms:
  - "/readyz returns ready=false"
  - "instance removed from load balancer / 5xx upstream"
signals:
  - health: /readyz
  - health: /healthz
  - metric: openblade_component_status
severity: high
automation_level: diagnostic_only
risk_level: low
status: verified
last_verified: 2026-07-19
verified_against: [openblade/nas/health_service.py:61, openblade/api/routes_health.py:51]
owners: [platform]
tags: [health, readiness, incident-response]
---

# RB-HEALTH-001 — Readiness failing

## Symptoms
`GET /readyz` returns `ready=false` with a `reason`.

## Preconditions
`/healthz` endpoint is reachable (if not, this is [API-001](service-not-starting.md)).

## Evidence collection (autonomous, read-only)
1. `GET /readyz` → capture `ready` + `reason`.
2. `GET /healthz` → capture per-component status (database / library / ltfs).
3. `openblade_component_status` metric per component (if scrape available).

## Diagnosis (decision tree — mind the verified quirk)
- `reason` contains **"database unavailable"** and `/healthz` database = `unhealthy`/`degraded`
  → DB problem → [RB-DB-001](database-unavailable.md).
- `reason` contains **"library unavailable"**:
  - `/healthz` library = `degraded` (connected, **0 drives**) → **not a hard outage**;
    the library is up but reports no drives (e.g. emulator seeded with 0 drives, or
    real backend discovery found none). Confirm before escalating as an outage.
  - `/healthz` library = `unhealthy` (exception) → backend init/connectivity failure.
- `reason` = "dependency check unavailable" → an exception in the health path itself.

## Remediation
**Diagnostic only.** There is no safe autonomous remediation for readiness failure:
the correct fix depends on the underlying cause (DB, backend config, drive
discovery). Route to the specific runbook and, if action is needed, follow its
approval-gated remediation. Do not restart blindly — a `degraded` library will still
fail readiness after a restart.

## Verification
After the underlying fix: `GET /readyz` → `ready=true`; `/healthz` all components `ok`.

## Escalation
Escalate if the cause is `library unhealthy` (exception) or DB `unhealthy`, or if the
`degraded`/0-drive state is unexpected for the environment.

## Audit
Record readiness `reason`, health component breakdown, chosen sub-runbook, outcome.
