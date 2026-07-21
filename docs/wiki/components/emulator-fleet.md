---
title: Component — emulator fleet
document_type: component
component: emulator
status: verified
last_verified: 2026-07-19
verified_against: [deploy/emulator/docker-compose.standalone.yml, deploy/emulator/Dockerfile.local]
owners: [platform]
tags: [component, emulator]
---

# Component: `emulator` (fleet ×3)

Standalone Quantum i3 emulators — the **same** `openblade.api.main:app` run with
`OPENBLADE_SCALAR_API_ONLY=true`.

| Property | Value |
|---|---|
| Instances | 3 (`library-1/2/3`) |
| Ports | 8010 / 8011 / 8012 |
| Profile | `scalar-i3-50-3` (50 slots, 3 drives, 60% occupancy, `instant` latency) |
| Health | `GET /health` |
| Persistence | per-lib data volume; **in-memory** AML state resets on restart |
| Start | `make emulator-up` / `make fleet-up` |

Failure: [EMU-001](../operations/failure-taxonomy.yaml). Runbook: [RB-EMU-001](../runbooks/emulator-unreachable.md).
Controller reaches instances via `OPENBLADE_EMULATOR_URLS`; fleet online/offline is derived from the `/health` probe.
