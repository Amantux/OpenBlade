---
title: ADR-0001 — Simulator-first backend default
document_type: adr
status: verified
last_verified: 2026-07-19
verified_against: [openblade/config.py:27, openblade/bootstrap.py, docs/architecture.md]
owners: [platform]
tags: [adr, backend, safety]
---

# ADR-0001 — Simulator-first backend default

**Status:** accepted (verified in code). **Context:** tape hardware is destructive
and scarce. **Decision:** default `OPENBLADE_BACKEND=mock`; real hardware is a narrow,
explicitly-gated path (`OPENBLADE_REAL_HARDWARE_ENABLED`). **Consequences:** dev/CI never
touch hardware; the Mock LTFS stores real bytes + sha256 so archive/restore is testable;
the agent must never enable hardware ([safety gates](../configuration/safety-gates.md)).
