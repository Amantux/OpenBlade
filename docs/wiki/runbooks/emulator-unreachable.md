---
title: Emulator fleet instance unreachable
document_type: runbook
component: emulator
services: [emulator, api]
failure_ids: [EMU-001]
symptoms:
  - "one emulator (8010/8011/8012) /health fails; others OK"
  - "fleet view shows a library offline"
signals:
  - health: /health
severity: medium
automation_level: approval_required
risk_level: medium
status: verified
last_verified: 2026-07-19
verified_against:
  - deploy/emulator/docker-compose.standalone.yml
  - openblade/api/routes_libraries.py
owners: [platform]
tags: [emulator, fleet, incident-response]
---

# RB-EMU-001 — Emulator instance unreachable

## Symptoms
`GET {emulator_url}/health` fails for one fleet instance while the others return 200;
the controller fleet probe marks that library offline.

## Preconditions
The other fleet instances are healthy (isolated failure). Environment is
`development` or `emulator` — **never `real`**.

## Evidence collection (autonomous, read-only)
1. Probe each emulator: `curl -m 3 http://localhost:8010/health` (…8011, …8012).
2. Container state for `emulator-library-{1,2,3}` (`make emulator-ps` / `docker ps`).
3. Logs of the failing instance for a crash/`Traceback`.
4. Confirm `host.docker.internal`/port mapping is intact (compose).

## Diagnosis
- Container exited/crashed → restart candidate.
- Container running but `/health` fails → app-level crash inside it → inspect logs first.
- Port conflict / mapping wrong → config fix, not a restart.

## Remediation
| Action | Scope | Env | Risk | Reversible | Rollback | Timeout | Success |
|---|---|---|---|---|---|---|---|
| `restart_emulator_instance` (**approval**) | 1 instance | dev/emulator | medium | no | none — **re-seed if state needed** | 120s | `/health` 200 + fleet online |

> The emulator holds **in-memory AML state**; a restart resets that instance's
> emulated inventory/audit/metrics. Never silent; requires approval. If seeded state
> matters, run `make seed-tapes` / `seed-libraries` after.

Preconditions (from catalog): EMU-001 confirmed; other instances healthy; unreachable >5m.

## Verification
`GET {emulator_url}/health` → 200; controller fleet probe shows the instance online;
a read (e.g. `/aml/system/status` on that instance) succeeds.

## Escalation
Escalate if: a second restart is needed; a new error signature appears; more than one
instance is affected (fleet-wide → likely host/compose issue, not a single instance).

## Audit
Record which instance, evidence, approval, restart outcome, whether re-seed was done,
verification result.
