---
title: Remaining Work
document_type: report
status: verified
last_verified: 2026-07-19
owners: [platform]
tags: [roadmap]
---

# Remaining Work

## Immediate blockers (before autonomous remediation)
- **Structured logging + request/correlation ID** (F-1) — required for reliable diagnosis.
- **Durable audit sink** (F-2) — required for the agent's own audit trail.
- **Behavioral agent-safety tests** ([agent-safety](../testing/agent-safety.md) Layer 2) —
  require an agent runtime; must pass before any write-action is enabled.

## Short-term
- Un-gate/parallel the Prometheus scrape (F-6); extend request metrics to native routes (F-4).
- Runtime-validate the 7 `proposed` runbooks against injected failures → mark `verified`.
- Fix config drift (F-7); resolve/removes Alembic ambiguity (F-8).

## Medium-term
- Fix the safety import-guard layering (F-3, route→service).
- Container/compose runtime validation in CI (currently verify-in-CI only).
- Wire a real notifier + escalation destinations.

## Long-term / architectural
- Optional distributed tracing (F-5) for controller↔emulator causality.
- Shared/persistent emulator state if horizontal scaling is needed (ADR-0003 trade-off).
- Real-hardware LTFS data-path (`drive_device`) + bench validation (F-11).
