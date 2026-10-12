---
title: Observability Gap Report
document_type: report
status: verified
last_verified: 2026-07-19
verified_against:
  - openblade/bootstrap.py:33
  - openblade/api/routes_aml_system.py:3305
  - openblade/api/aml_state.py
  - openblade/api/main.py:171
owners: [platform]
tags: [observability, gaps, agent-readiness]
---

# Observability Gap Report

Which failures the agent **cannot reliably detect or diagnose today**, and why. This
gates automation: no autonomous remediation for a failure whose detection is `limited`.

## Gaps (verified)

| # | Gap | Impact on agent | Affected failures | Severity | Proposed fix |
|---|---|---|---|---|---|
| G1 | **Logs unstructured, no request/correlation ID** (structlog default config, no request middleware) | Cannot correlate a request across logs; single-line matches unreliable → caps confidence | all log-based | high | JSON renderer + request-id middleware + `service`/`env` binding |
| G2 | **Prometheus scrape endpoint is auth-gated** | Standard scrapers can't pull metrics without a token → metrics may be `unknown` | all metric-based | high | dedicated unauthenticated `/metrics` (network-restricted) or scrape token |
| G3 | **Metrics are in-memory, reset on restart** | Zeroed counter ≠ recovery; no history | JOB-001, API-002 | medium | export via `prometheus_client` + retention in Prometheus |
| G4 | **Request metrics cover only `/aml`+`/iblade`** | No latency/error signal for `/archive`,`/restore`,`/jobs`,`/catalog` | API-002 (partial) | medium | extend middleware to native routes |
| G5 | **No disk/memory metrics from the app** | DISK-001, MEM-001 detection depends on host/node telemetry not in OpenBlade | DISK-001, MEM-001 | high | node-exporter alongside; or add process gauges |
| G6 | **Audit + login activity are in-memory, capped, non-durable** | No durable agent audit trail; login-failure history lost on restart | AUTH-001, agent audit | high | durable append-only audit sink (see [audit requirements](../agent/audit-requirements.md)) |
| G7 | **No distributed tracing** | No cross-service causal chain for multi-hop failures (controller→emulator) | fleet/latency diagnosis | medium | OpenTelemetry (optional) |
| G8 | **Health not exported as a scrapeable gauge** | No `up`/health metric; alerts rely on `openblade_component_status` only | API-001 (unreachable) | low | expose a health gauge / blackbox probe |
| G9 | **Unbounded in-memory metric keys** (per-endpoint latency map) | Potential memory growth (feeds MEM-001) | MEM-001 | low | cap/rotate endpoint keys |

## Consequence for automation

- Failures with `detection.status: limited` in [`failure-taxonomy.yaml`](failure-taxonomy.yaml)
  (AUTH-001, DISK-001, MEM-001) are **notify/escalate only** — never autonomous.
- Until **G1, G2, G6** are addressed, the agent operates in **diagnostic + approval**
  mode; high-confidence autonomous write-actions are not justified.
