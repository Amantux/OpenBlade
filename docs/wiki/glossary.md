---
title: Glossary and Canonical Names
document_type: glossary
status: verified
last_verified: 2026-07-19
verified_against: [docker-compose.yml, openblade/api/main.py, Makefile]
owners: [platform]
tags: [glossary, naming, agent]
---

# Glossary and Canonical Names

The agent MUST use these **canonical names** in alerts, diagnoses, and incident
notes. Aliases are listed so untrusted/legacy text can be normalized.

## Components (canonical → aliases)

| Canonical | Aliases | What it is |
|---|---|---|
| `api` | backend, control-plane, FastAPI app, `openblade.api.main` | The FastAPI service (control plane + emulator). Port 8000. |
| `web` | frontend, React UI, ui | React/Vite UI served by nginx. Port 5173→80. |
| `web_flask` | flask-ui, nas-console | Flask NAS operator console. Port 8080. |
| `cli` | `openblade` command | Typer CLI. |
| `emulator` | i3 emulator, library-1/2/3, scalar-i3 | Standalone i3 emulator instance. Ports 8010–8012. |
| `emulator-ui` | — | nginx proxy UI for the emulator fleet. Port 5174. |
| `catalog` | database, db, sqlite, catalog-db | SQLite metadata store. |
| `aml_state` | AMLState, emulator state | In-memory emulator/audit/metrics singleton (non-durable). |
| `assistant` | ai-assistant, helper agent | OpenAI-compatible read-only helper. |
| `jobs` | job-queue, worker | In-process `JobQueue`+`Worker`. |
| `simulator` | mock backend | In-memory Mock library + Mock LTFS. |
| `real-backend` | hardware backend | SCSI `mtx` + host LTFS (guarded). |
| `scalar_http` | webservices backend, AML client | Real-i3 AML Web Services client backend. |

## Environments

| Canonical | Meaning |
|---|---|
| `development` | local / `OPENBLADE_ENV=development` (default). |
| `emulator` | `I3_TEST_MODE=emulator` / `OPENBLADE_SCALAR_API_ONLY=true` fleet. |
| `real` | `OPENBLADE_BACKEND=real` + `OPENBLADE_REAL_HARDWARE_ENABLED=true`. |
| `ci` | GitHub Actions. |

## Health states (canonical) — `verified` against `openblade/nas/health_service.py`

`ok` · `degraded` · `unhealthy` · plus wiki-level `unavailable` · `unknown`. See
[health model](operations/health-model.md).

## Key metrics (canonical, exact names) — `verified`

`openblade_component_status`, `openblade_iblade_request_total`,
`openblade_iblade_request_duration_ms`, `openblade_jobs_state_total`,
`openblade_transfer_activity_total`, `openblade_transfer_throughput_files_per_second`,
`openblade_media_utilization_percent`, `openblade_cleaning_media_total`,
`openblade_system_uptime_seconds` (full list in [observability](operations/observability.md)).

## Safety terms — `verified`

| Term | Meaning |
|---|---|
| safety token | One-time token required to format a tape (barcode-scoped). |
| format confirmation | Barcode + safety token gate before `mkltfs`/erase. |
| unload guard | Unload is rejected while LTFS is mounted or dirty. |
| real-hardware gate | Real ops require `OPENBLADE_BACKEND=real` AND `OPENBLADE_REAL_HARDWARE_ENABLED=true`. |

## Failure ID convention

`AREA-NNN`, area ∈ {`API`, `DB`, `HEALTH`, `EMU`, `JOB`, `AUTH`, `HW`, `NAS`,
`ASST`, `DISK`, `MEM`}. Failure IDs are unique across the wiki (CI-enforced).
See [failure taxonomy](operations/failure-taxonomy.md).

## Runbook ID convention

`RB-AREA-NNN`, mapped 1:1 to one or more failure IDs. Unique (CI-enforced).
