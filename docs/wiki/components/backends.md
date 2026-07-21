---
title: Component — backends (simulator / real / webservices)
document_type: component
component: real-backend
status: verified
last_verified: 2026-07-19
verified_against: [openblade/bootstrap.py:361, openblade/hardware/, openblade/simulator/, openblade/config.py]
owners: [platform]
tags: [component, backend, hardware]
---

# Component: backends

Selected by `OPENBLADE_BACKEND` (+ `OPENBLADE_ROBOTICS_TRANSPORT` when `real`).

| Mode | Robotics | Data path | Guarded? |
|---|---|---|---|
| `mock`/`simulator` (default) | Mock library (in-memory) | Mock LTFS (real bytes + sha256) | no |
| `real` + `scsi` | `mtx` → `/dev/smc*` | host LTFS → `/dev/st*` | yes |
| `real` + `webservices` | `scalar_http` AML client (`moveMedium`) | host LTFS | yes |

## Real-hardware gate (verified)
`real` requires `OPENBLADE_BACKEND=real` AND `OPENBLADE_REAL_HARDWARE_ENABLED=true`
(`openblade/hardware/safety.py`), else `RealHardwareDisabledError`. See [safety gates](../configuration/safety-gates.md) and [RB-HW-001](../runbooks/real-hardware-blocked.md).

## Notes
- `webservices` control plane is validated against the emulator; the LTFS **data path**
  on real hardware (`drive_device` serial↔`/dev/st`) is a milestone-2 item.
- The `real`/hardware path is `real_hardware`-marked in tests (not run in CI).
