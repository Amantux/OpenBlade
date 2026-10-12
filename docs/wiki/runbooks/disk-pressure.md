---
title: Disk pressure on data volume
document_type: runbook
component: catalog
services: [api]
failure_ids: [DISK-001]
severity: high
automation_level: none
risk_level: medium
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-DISK-001 — Disk pressure on data volume

## Symptoms
DB write errors; `disk I/O error` in logs; host filesystem for `/data` near full.

## Evidence (read-only — requires HOST telemetry; app emits no disk metric)
1. Host/container filesystem usage for the DB path. 2. DB write errors in logs. 3. Growth trend.

## Diagnosis
Disk pressure on the SQLite volume. **Gap:** OpenBlade exports no disk metric —
detection depends on node/host telemetry (see [observability gap report](../operations/observability-gap-report.md) G5).

## Remediation
**None autonomous.** Freeing space / expanding the volume is a human/infra action.
The agent must NOT delete data. Escalate with the usage evidence.

## Verification
Free space restored; DB writes succeed; `/healthz` database `ok`.

## Escalation
High — data-loss risk. Escalate to platform/infra.

## Audit
Record usage evidence, escalation, that no data was deleted by the agent.
