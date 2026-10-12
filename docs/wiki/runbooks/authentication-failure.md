---
title: Authentication failures
document_type: runbook
component: api
services: [api]
failure_ids: [AUTH-001]
severity: medium
automation_level: none
risk_level: low
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-AUTH-001 — Authentication failures

## Symptoms
Operators cannot log in; repeated `Invalid credentials` in logs.

## Evidence (read-only, LIMITED telemetry)
1. `GET /aml/users/sessions` / login activity (in-memory, capped 500 — may be reset on restart).
2. A `synthetic_login_probe` (dev/emulator) to reproduce.
3. Login mode (local vs LDAP) and whether defaults changed.

## Diagnosis
- Broad failure across users → identity/config problem (LDAP mode misconfig, default creds changed).
- Single user → that account.
- After a restart → session store reset (in-memory) is expected, not an outage.

## Remediation
**None autonomous.** Credential/identity changes are human + security-sensitive. Escalate.

## Verification
A synthetic login succeeds after the human fix.

## Escalation
Escalate to `security` for identity-provider or credential issues.

## Audit
Record scope (which users), evidence, escalation. Never log credentials.
