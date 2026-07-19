---
title: Elevated request latency
document_type: runbook
component: api
services: [api]
failure_ids: [API-002]
severity: medium
automation_level: diagnostic_only
risk_level: low
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-API-002 — Elevated request latency

## Symptoms
`OpenBladeHighLatency` firing: `max(openblade_iblade_request_duration_ms{stat=avg}) > 1500` for >10m.

## Evidence (read-only)
1. Latency metric by endpoint/method. 2. `EMULATOR_LATENCY_PROFILE` (a high profile inflates latency by design). 3. Backend + host load.

## Diagnosis
- Latency profile set high → **expected**, not an incident (config).
- Profile `instant` but latency high → backend slowness or resource pressure ([MEM-001](../operations/failure-taxonomy.yaml)).
- **Gap:** only `/aml`+`/iblade` are measured; `/archive|/restore|/jobs` latency is invisible to metrics.

## Remediation
Diagnostic only. Adjusting the latency profile is a **human config change**. No autonomous action.

## Verification
Latency returns below threshold; profile confirmed intended.

## Escalation
Escalate if profile is `instant` yet latency is high (points to backend/resource issue).

## Audit
Record latency values, profile, conclusion.
