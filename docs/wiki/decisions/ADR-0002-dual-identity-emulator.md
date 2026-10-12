---
title: ADR-0002 — One app is both controller and i3 emulator
document_type: adr
status: verified
last_verified: 2026-07-19
verified_against: [openblade/api/main.py, openblade/config.py:52]
owners: [platform]
tags: [adr, emulator, architecture]
---

# ADR-0002 — One app is both controller and emulator

**Status:** accepted (verified). **Decision:** `openblade.api.main:app` serves native
control-plane surfaces AND the Quantum `/aml`+`/iblade` emulator; `OPENBLADE_SCALAR_API_ONLY=true`
hides native surfaces for standalone emulator instances. **Consequences:** the emulator
and controller share a contract (models reused for the real-i3 client); operationally the
same process/health/metrics apply to both roles ([system overview](../system-overview.md)).
