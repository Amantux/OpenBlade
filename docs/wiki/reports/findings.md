---
title: Findings Report
document_type: report
status: verified
last_verified: 2026-07-19
owners: [platform]
tags: [findings, risks]
---

# Findings Report

Grouped by severity. Each is verified against code/runtime.

## High
- **F-1 · Observability: no structured logs / request-id.** structlog default config
  (`bootstrap.py:33`); no request/correlation ID; no access-log middleware. *Impact:*
  the agent cannot correlate a request across logs; single-line matches unreliable.
  *Remediation:* JSON renderer + request-id middleware. *Effort:* M. *Tests:* logging
  assertions. *Residual:* until then, agent stays diagnostic+approval.
- **F-2 · Non-durable audit/login.** `_record_audit`/`login_activity` in-memory, capped,
  reset on restart (`aml_state.py`). *Impact:* no durable agent audit trail; lost auth
  history. *Remediation:* append-only external audit sink. *Effort:* M.
- **F-6 · Metrics scrape auth-gated + in-memory.** `/…/prometheus` requires auth and
  resets on restart. *Impact:* standard scrapers can't pull; no history. *Effort:* M.

## Medium
- **F-3 · Safety import-guard failing.** `test_import_guard` finds routes calling
  backends directly (layering). Not in required CI. *Remediation:* route→service or
  allowlist. *Effort:* M. *Residual:* mock-mode harmless; matters for real-hw layering.
- **F-4 · Request metrics cover only `/aml`+`/iblade`.** `/archive|/restore|/jobs`
  latency invisible. *Effort:* S–M.
- **F-7 · Config drift.** compose `mock` vs Dockerfile `simulator`; Flask port 8080 vs
  README 5173; Flask not in main compose. *Effort:* S.

## Low
- **F-5 · No tracing** (no OpenTelemetry). *Effort:* L (optional).
- **F-8 · Alembic declared but unused.** Schema via `create_all`. Document/remove. *Effort:* S.
- **F-9 · Unbounded in-memory metric keys** (per-endpoint map) → memory growth vector. *Effort:* S.

## Informational
- **F-10 · `auto-merge-trusted.yml`** squash-merges trusted-author PRs after CI — a
  supply-chain trust decision (documented in [trust boundaries](../architecture/trust-boundaries.md)).
- **F-11 · Real-hardware LTFS data path** (`drive_device`) unimplemented; robotics
  control-plane verified, data path is milestone-2.
