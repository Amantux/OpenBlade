---
title: Real-hardware op blocked by safety gate
document_type: runbook
component: real-backend
services: [api]
failure_ids: [HW-001]
severity: low
automation_level: none
risk_level: low
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-HW-001 — Real-hardware op blocked by safety gate

## Symptoms
`RealHardwareDisabledError`; log `Real hardware operations require ...`.

## Diagnosis (this is a control working as intended)
`OPENBLADE_BACKEND != real` or `OPENBLADE_REAL_HARDWARE_ENABLED != true`. Not an outage.

## Remediation
**None.** The agent MUST NOT enable hardware. Enabling real hardware is a deliberate
human decision (sets the two env vars) after safety review. See [safety gates](../configuration/safety-gates.md).

## Verification
n/a (no remediation). If a human enables hardware, verify with `openblade hardware connect-i3`.

## Escalation
Notify the operator that hardware is disabled; do not act.

## Audit
Record the blocked attempt and that no action was taken.
