---
title: Catalog database unavailable
document_type: runbook
component: catalog
services: [api]
failure_ids: [DB-001]
severity: high
automation_level: approval_required
risk_level: medium
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-DB-001 — Catalog database unavailable

## Symptoms
`/healthz` database component `unhealthy`/`degraded`; log `database health probe failed`.

## Preconditions
`/healthz` reachable; issue isolated to the DB component.

## Evidence collection (read-only)
1. `/healthz` component breakdown (which probes fail: datasets/path_mappings/cartridges/rebuild_runs).
2. `OPENBLADE_DB_URL`/`OPENBLADE_DB_PATH` resolves to a writable path.
3. Free space on the DB volume (host telemetry — see [RB-DISK-001](disk-pressure.md)).
4. Logs for SQLite lock/IO/permission errors.

## Diagnosis
- All probes fail → DB file missing/corrupt/locked, or disk full.
- Some probes fail → partial schema / one table issue.
- Permission error → `/data` ownership (container `docker-entrypoint.sh` chowns `/data`).

## Remediation
Diagnostic-first. **No autonomous DB action.** If disk-full → [RB-DISK-001](disk-pressure.md).
DB restore = **human, approval-only** (restore a file backup; never over live data without approval).
There are **no migrations** to run (schema via `create_all`).

## Verification
`/healthz` database = `ok`; `/readyz ready=true`; a catalog read succeeds.

## Escalation
Escalate on corruption suspicion or restore need. Preserve the DB file before any action.

## Audit
Record failing probes, cause, any human-approved restore, verification.
