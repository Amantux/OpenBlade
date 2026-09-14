---
title: Agent Action Policy (allowlist / denylist)
document_type: policy
status: proposed
last_verified: 2026-07-19
verified_against: [agent/remediation-catalog.yaml, operations/failure-taxonomy.yaml]
owners: [platform, security]
tags: [agent, action, safety]
---

# Agent Action Policy

The authoritative, machine-readable policy is
[`remediation-catalog.yaml`](remediation-catalog.yaml). This page is its human summary.

## Autonomous (allowlisted) — read-only diagnostics only

| Action | Env | Risk | Why safe |
|---|---|---|---|
| `rerun_health_check` | all | low | idempotent read of `/healthz`,`/readyz` |
| `collect_diagnostic_evidence` | all | low | read-only logs/metrics/health/config |
| `synthetic_login_probe` | dev/emulator | low | single probe-account attempt |

No state-changing action is autonomous today (see rationale in the catalog:
OpenBlade lacks stateless-with-redundancy components; emulators hold in-memory state).

## Approval-required (never silent)

| Action | Env | Risk |
|---|---|---|
| `restart_emulator_instance` | dev/emulator only | medium (loses in-memory AML state) |
| `restart_api_process` | dev only | high |

## Denied — human-only, never autonomous

Format/erase tape · unload-while-mounted · enable real hardware · move real media ·
delete/modify catalog records · DB migrations · restore backup over live data ·
rotate/read secrets · modify auth/RBAC/security controls · modify DNS/firewall/ports ·
deploy/merge code · clear durable state · **anything not in the allowlist**.

## Environment gate (`verified` constraint)

Any hardware-adjacent action in the `real` environment is **denied** to the agent.
Real hardware requires deliberate human enablement (`OPENBLADE_BACKEND=real` +
`OPENBLADE_REAL_HARDWARE_ENABLED=true`) — the agent must escalate, never set these.

## Preconditions & verification are mandatory

An allowlisted/approved action executes only when every `preconditions` item holds,
within `max_scope`, and is followed by its `verification` checks. On verification
failure the action stops and escalates ([escalation policy](escalation-policy.md)).
