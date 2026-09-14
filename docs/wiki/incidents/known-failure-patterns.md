---
title: Known Failure Patterns
document_type: report
status: provisionally_verified
last_verified: 2026-07-19
owners: [platform]
tags: [incidents, patterns]
---

# Known Failure Patterns

Seeded from code review + this session's observed behavior. Each is a *pattern to
watch*, not a confirmed production incident. The agent may use these as qualified
context only when current evidence is consistent (see [confidence policy](../agent/confidence-policy.md)).

| Pattern | Evidence (observed) | Related failure | Note |
|---|---|---|---|
| Cold `create_context` latency | first request ~4–5s after start/reset (measured this session) | — | expected on startup; not an incident. See [test-speed task]. |
| Metrics reset on restart | in-memory `aml_state`; counters zero after restart | JOB-001 false-signal | zeroed ≠ recovered |
| Readiness fails on 0-drive library | `/readyz` false while library `degraded` | HEALTH-001 | not a DB outage |
| Latency-profile inflation | high latency with `EMULATOR_LATENCY_PROFILE != instant` | API-002 | config, not fault |
| Auth-guard vs service-token layering | direct backend calls in routes flagged by `test_import_guard` | — | architectural, non-runtime |

New incidents are recorded using [`incident-schema.json`](incident-schema.json).
