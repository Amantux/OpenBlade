---
title: Readiness Score
document_type: report
status: verified
last_verified: 2026-07-19
owners: [platform]
tags: [readiness, scorecard]
---

# Readiness Score

Scores 0–5, evidence-based. No production traffic exists, so these are directional
(not a precise SLA judgment).

| Area | Score | Evidence | Biggest gap |
|---|---:|---|---|
| Architecture clarity | 4 | dual-identity + backends + trust boundaries documented & verified | web_flask/compose drift |
| Build reproducibility | 3 | native + frontend build verified; runtime smoke 8/8 | container paths verify-in-CI only |
| Automated testing | 4 | ~1,120 tests; core green | real-hw unrun; 1 guard fail |
| Failure detection | 2 | 5 alerts on real metrics | no telemetry for auth/disk/memory |
| Observability | 2 | 14 metrics + health endpoints | no structured logs/request-id/durable audit/tracing |
| Runbook quality | 3 | standard structure; 3 verified | 7 proposed need runtime validation |
| Agent retrieval quality | 4 | metadata + canonical names + validator | no live retrieval eval yet |
| Autonomous remediation safety | 4 | conservative catalog, test-enforced, correctly disabled | no behavioral tests yet |
| Escalation & notifications | 3 | policy + templates defined | no live notifier wired |
| Incident memory | 3 | schema defined | no records yet |
| Documentation freshness | 4 | CI-validated, last_verified enforced | manual re-verify cadence |
| Security & least privilege | 3 | capability model + kill switch defined | kill switch `proposed`; default secrets warn |
| Production readiness | 2 | works in dev/emulator | observability + durable audit blockers |

## Overall

**≈ 55% agent-ready.** The *knowledge, policy, and validation* layer is strong and
machine-enforced. The gating deficits are **observability** (structured logs,
request IDs, durable audit) and **runtime-validated runbooks**. Autonomous
remediation remains correctly disabled until those close.
