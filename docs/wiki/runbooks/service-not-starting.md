---
title: API not starting / unreachable
document_type: runbook
component: api
services: [api]
failure_ids: [API-001]
symptoms:
  - "/healthz connection refused or timeout"
  - "UI shows backend unreachable; CLI errors"
signals:
  - health: /healthz
  - log_pattern: "Traceback (most recent call last)"
severity: critical
automation_level: approval_required
risk_level: high
status: verified
last_verified: 2026-07-19
verified_against: [Dockerfile, openblade/api/main.py, openblade/bootstrap.py, docker-entrypoint.sh]
owners: [platform]
tags: [api, startup, incident-response]
---

# RB-API-001 — API not starting / unreachable

## Symptoms
`GET http://<api>:8000/healthz` refuses connection or times out; dependent UI/CLI fail.

## Preconditions
Only the `api` component is affected (if the whole host is down, this is a host incident, not API-001).

## Evidence collection (autonomous, read-only — `collect_diagnostic_evidence`)
1. `curl -sS -m 5 http://<api>:8000/healthz` — expect connection refused/timeout.
2. Process check: is `uvicorn openblade.api.main:app` running? (container: `docker ps`/logs).
3. Last ~200 log lines (stdout) — look for a startup `Traceback`, `Address already in use`, or DB path errors.
4. Config: `OPENBLADE_DB_URL`/`OPENBLADE_DB_PATH` points at a **writable** path (`/data` in containers; `docker-entrypoint.sh` chowns `/data`).
5. Recent deployments/config changes.

## Diagnosis (decision tree)
- Log shows `Address already in use` → **port 8000 conflict**.
- Log shows `ImportError`/`Traceback` at import → **code/config error** (recent deploy suspect).
- Log shows DB open/permission error → **DB path not writable / disk** (see [RB-DB-001](database-unavailable.md), [RB-DISK-001](disk-pressure.md)).
- No process, no log → **crashed on start / never started**.

## Remediation
| Action | Scope | Env | Risk | Reversible | Rollback | Timeout | Success |
|---|---|---|---|---|---|---|---|
| `restart_api_process` (approval) | 1 process | **development only** | high | no | none (re-launch prior version) | 60s | `/readyz ready=true` |
| Fix config/port then restart | 1 process | dev | high | n/a | revert config | — | health ok |

The agent MUST NOT restart in `staging`/`real`; escalate with the evidence bundle.
Preconditions for restart: no in-flight archive/restore job; data volume writable.

## Verification
`GET /healthz` → 200 with all components `ok`; `GET /readyz` → `ready=true`. Run one
read workflow (e.g. `GET /aml/system/status` after login).

## Escalation
Escalate immediately if: not `development`; a second restart would be needed;
`restart_loop_detected`; or root cause is a bad deploy (recommend revert to a human).

## Audit
Record trigger, evidence (with citations), decision (`approval_required`), approver,
restart outcome, verification result. See [audit requirements](../agent/audit-requirements.md).
