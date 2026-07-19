---
title: Normal Behavior Baselines
document_type: operations
status: provisionally_verified
last_verified: 2026-07-19
verified_against:
  - deploy/emulator/docker-compose.standalone.yml
  - openblade/api/main.py
  - Makefile
owners: [platform]
tags: [baseline, normal, agent]
---

# Normal Behavior Baselines

The agent cannot detect abnormal until normal is defined. **No production data
exists**, so thresholds are labeled by origin — do **not** treat provisional/proposed
numbers as production SLOs.

Label legend: `measured` (observed here) · `config-derived` (from config) ·
`code-derived` (from code) · `provisional` (reasonable dev default) · `proposed`.

## API (`api`)

| Signal | Baseline | Label |
|---|---|---|
| process | 1 uvicorn worker on :8000 | config-derived (Dockerfile) |
| `/healthz` | 200, all components `ok`, in <1s | code-derived |
| `/readyz` | 200, `ready=true` when DB+library ok | code-derived |
| startup | app + `create_context` seeding; first request slow (~4–5s cold `create_context`) | measured (this session) |
| request latency (`/aml`,`/iblade`) | governed by `EMULATOR_LATENCY_PROFILE` (`instant`=0ms) | config-derived |
| error rate | ~0 in steady state | provisional |

## Emulator fleet (`emulator`)

| Signal | Baseline | Label |
|---|---|---|
| instances | 3 (8010/8011/8012) | config-derived (standalone compose) |
| profile | `scalar-i3-50-3` = 50 slots, 3 drives, 60% occupancy, `instant` latency | config-derived |
| `/health` | 200 per instance | code-derived |

## Jobs (`jobs`)

| Signal | Baseline | Label |
|---|---|---|
| execution | synchronous via `enqueue` (no broker) | code-derived |
| `openblade_jobs_state_total{queue=active}` | 0 at rest; >0 briefly during archive/restore | code-derived |
| `active_mounts` | >0 while a job holds a drive | code-derived |

## Catalog (`catalog`)

| Signal | Baseline | Label |
|---|---|---|
| DB probes (`/healthz`) | all pass | code-derived |
| DB file | `/data/openblade.db` (container) writable | config-derived |

## Startup / shutdown

| Signal | Baseline | Label |
|---|---|---|
| API cold start | seconds (import + seeding) | measured |
| clean shutdown | uvicorn SIGTERM; in-memory `aml_state` lost (expected) | code-derived |
| restart persistence | SQLite catalog persists; `aml_state`/audit/metrics reset | verified |

Deviations beyond these baselines are candidate signals for the
[failure taxonomy](failure-taxonomy.yaml); corroborate before acting.
