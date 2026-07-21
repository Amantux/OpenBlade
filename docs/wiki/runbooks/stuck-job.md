---
title: Job queue stalled
document_type: runbook
component: jobs
services: [api]
failure_ids: [JOB-001]
severity: high
automation_level: approval_required
risk_level: medium
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-JOB-001 — Job queue stalled

## Symptoms
`OpenBladeQueueStalled` firing: active jobs > 0 but `active_mounts == 0` for >10m.

## Evidence (read-only)
1. `openblade_jobs_state_total{queue=active}` and `openblade_transfer_activity_total{metric=active_mounts}`.
2. Logs around the stuck job; recent job history.
3. Health of the active backend (a blocked mount can stall the in-process worker).

## Diagnosis
Because jobs run **in-process** (no broker), a stall usually means the worker thread is
blocked on a backend/mount operation — not a lost message.

## Remediation
No safe autonomous restart (the api process owns the job worker; restarting it is
[RB-API-001](service-not-starting.md) territory, dev-only, approval). Retrying a job
requires confirmed idempotency — **approval_required**. Prefer collecting evidence and escalating.

## Verification
Active job count decreases; `active_mounts` returns to >0 during work; no new error signature.

## Escalation
Escalate if a backend/mount is blocked or the worker cannot make progress.

## Audit
Record queue metrics, backend health, decision, outcome.
