---
title: Runbook Index
document_type: index
status: verified
last_verified: 2026-07-19
owners: [platform]
tags: [runbooks, index]
---

# Runbook Index

Each runbook maps to one or more [failure IDs](../operations/failure-taxonomy.yaml)
and follows the standard structure (Metadata · Symptoms · Preconditions · Evidence
Collection · Diagnosis · Remediation · Verification · Escalation · Audit). Remediation
authority is defined only by [`remediation-catalog.yaml`](../agent/remediation-catalog.yaml).

| Runbook ID | Title | Failure IDs | Severity | Automation | Status |
|---|---|---|---|---|---|
| [RB-API-001](service-not-starting.md) | API not starting / unreachable | API-001 | critical | approval (dev restart) | verified |
| [RB-HEALTH-001](readiness-failing.md) | Readiness failing (`/readyz`) | HEALTH-001 | high | diagnostic_only | verified |
| [RB-DB-001](database-unavailable.md) | Catalog DB unavailable | DB-001 | high | approval_required | proposed |
| [RB-EMU-001](emulator-unreachable.md) | Emulator fleet instance unreachable | EMU-001 | medium | approval (dev/emulator) | verified |
| [RB-JOB-001](stuck-job.md) | Job queue stalled | JOB-001 | high | approval_required | proposed |
| [RB-API-002](high-latency.md) | Elevated request latency | API-002 | medium | diagnostic_only | proposed |
| [RB-AUTH-001](authentication-failure.md) | Authentication failures | AUTH-001 | medium | none (limited telemetry) | proposed |
| [RB-HW-001](real-hardware-blocked.md) | Real-hardware op blocked by safety gate | HW-001 | low | none (by design) | proposed |
| [RB-ASST-001](assistant-endpoint-down.md) | Assistant endpoint failing | ASST-001 | low | diagnostic_only | proposed |
| [RB-DISK-001](disk-pressure.md) | Disk pressure on data volume | DISK-001 | high | none (host telemetry) | proposed |

**Status legend:** `verified` runbooks were validated against code/behavior this
session; `proposed` runbooks are structurally complete but their remediation steps
await validation + agent-safety tests before any automation is enabled.
