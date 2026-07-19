---
title: Failure Taxonomy (human view)
document_type: failure-taxonomy
status: verified
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [failures, taxonomy, agent]
---

# Failure Taxonomy

Machine-readable source: [`failure-taxonomy.yaml`](failure-taxonomy.yaml) (authoritative;
CI-validated: unique IDs, metric references must exist). Symptom→evidence→action summary:

| Failure ID | Symptom | Likely cause | Required evidence (≥2) | Safe action | Approval | Verification |
|---|---|---|---|---|---|---|
| API-001 | `/healthz` refused/timeout | api crashed / port / DB path | health fail + process absent | restart (dev) | yes (dev) | `/readyz ready=true` |
| HEALTH-001 | `/readyz ready=false` | DB or library not `ok` | readyz reason + healthz breakdown | none (route to sub-runbook) | — | `ready=true` |
| DB-001 | DB probes fail | file missing/locked/corrupt/full | healthz db + log pattern | none autonomous | yes | db `ok` |
| EMU-001 | one emulator `/health` fails | container down/crash | probe fail + others ok | restart 1 (dev/emu) | yes | instance online |
| JOB-001 | queue stalled | worker/mount blocked | jobs>0 + active_mounts==0 (10m) | none autonomous | yes | queue drains |
| API-002 | high latency | profile high / backend slow | HighLatency alert + duration metric | none | — | below threshold |
| HW-001 | RealHardwareDisabledError | gate off (by design) | error + env flags | none (never bypass) | — | n/a |
| ASST-001 | `/assistant/chat` 502 | LLM endpoint down/misconfig | status/reachability | none | — | chat works |
| AUTH-001 | login failures | creds/LDAP/reset | repeated failures + probe | none | — | synthetic login ok |
| DISK-001 | DB write errors | disk full | host usage + log | none (never delete) | — | space restored |
| MEM-001 | OOM/restarts | leak / growth | host RSS + restart correlation | none | — | stable |

**Rule (verified):** never reduce diagnosis to a single log line; each failure lists the
corroborating evidence required before the agent may act or claim high confidence.
