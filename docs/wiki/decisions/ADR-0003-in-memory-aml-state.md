---
title: ADR-0003 — Emulator/audit state is in-memory
document_type: adr
status: verified
last_verified: 2026-07-19
verified_against: [openblade/api/aml_state.py]
owners: [platform]
tags: [adr, state, observability]
---

# ADR-0003 — Emulator/audit/metrics state is in-memory

**Status:** accepted (verified), with known trade-offs. **Decision:** emulator inventory,
audit log, login activity, and latency metrics live on a module-global `AMLState` and are
not persisted. **Consequences (operational):** these reset on restart and are per-process
(not shared across replicas); the agent must treat zeroed metrics as "restarted, not
recovered", must not rely on `aml_state` for a durable audit trail, and requires approval to
restart an emulator (state loss). See [observability gap report](../operations/observability-gap-report.md) G3/G6.
