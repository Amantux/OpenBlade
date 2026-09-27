---
title: Agent Confidence Policy
document_type: policy
status: proposed
last_verified: 2026-07-19
owners: [platform]
tags: [agent, confidence]
---

# Agent Confidence Policy

Confidence is **evidence-based**, not an LLM-generated probability. It is computed
from independent signals: health checks, metrics, logs, deployment history, config
state, dependency health, prior incident consistency, and direct command results.

## Bands

| Band | Definition |
|---|---|
| **high** | ≥2 independent signals support one diagnosis AND the failure's `evidence_required` list is fully satisfied AND no strong conflicting evidence. |
| **medium** | Evidence supports a likely diagnosis but a meaningful alternative remains, or one required signal is missing. |
| **low** | Evidence is incomplete, contradictory, or stale. |
| **unknown** | Required telemetry is unavailable (e.g. DISK-001/MEM-001 — no metric exported). |

## Diagnosis-confidence × action-risk matrix

| Diagnosis confidence | Action risk | Default behavior |
|---|---|---|
| high | low | Execute automatically **only if** allowlisted in `remediation-catalog.yaml` and preconditions met |
| high | medium | Request approval (unless a narrowly-scoped policy permits) |
| high | high | Escalate and recommend; do **not** execute |
| medium | low | Gather more evidence or request approval |
| medium | medium/high | Escalate |
| low | any | Notify with evidence + uncertainty; do **not** remediate |
| unknown | any | Notify; state missing telemetry; do **not** remediate |

## OpenBlade-specific confidence caveats (`verified`)

- **Single log line is insufficient.** Logs are unstructured (structlog default, no
  request/correlation ID). Never reach `high` from one log match.
- **Metrics reset on restart** and are **in-memory** — a zeroed counter may mean
  "restarted", not "recovered". Corroborate with uptime + health.
- **`/aml`+`/iblade` only** for latency/request metrics — high confidence on
  `/archive|/restore|/jobs` latency is not achievable from metrics alone.
- **Readiness quirk:** `/readyz` fails on a drive-less-but-connected library; do not
  conclude "DB down" without the `/healthz` component breakdown.
- Prior incidents raise confidence **only** when current evidence is consistent.
