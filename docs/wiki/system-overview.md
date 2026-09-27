---
title: System Overview
document_type: overview
status: verified
last_verified: 2026-07-19
verified_against:
  - openblade/api/main.py
  - openblade/bootstrap.py
  - docker-compose.yml
  - Makefile
  - pyproject.toml
owners: [platform]
tags: [overview, architecture]
---

# System Overview

OpenBlade is a **simulator-first controller and emulator** for a Quantum Scalar i3
LTO tape library. One FastAPI application plays two roles depending on
configuration; a React UI and an optional Flask NAS console sit in front of it; a
Typer CLI drives the same service graph; and a fleet of emulator instances stands
in for real i3 libraries.

## What it does (verified capabilities)

- Emulate the Quantum i3 **AML** (`/aml/*`) and **iBlade** (`/iblade/*`) Web Services surfaces.
- Archive/restore files to tape with **sharding** (STRIPE / BLOCK_STRIPE) and checksum verification.
- Manage a **NAS** layer (pools, shares, policies, datasets, cache-drive/source-stream ingest).
- Control a **real** or **emulated** i3 via three backend modes (simulator / SCSI / Web Services client).
- Enforce **safety gates** for destructive/hardware operations (see [safety gates](configuration/safety-gates.md)).
- Provide an in-app **AI assistant** (read-only helper agent).

## The dual identity (critical mental model) — `verified`

`openblade.api.main:app` serves **both**:

- OpenBlade-native surfaces: `/inventory`, `/jobs`, `/archive`, `/restore`, `/catalog`, `/nas`, `/storage`, `/api`, `/assistant`.
- Quantum emulator surfaces: `/aml/*` and `/iblade/*`.

`OPENBLADE_SCALAR_API_ONLY=true` flips the same app into **emulator-only** mode:
only matrix-documented `/aml` + `/iblade` endpoints (plus `/health`, `/docs`,
`/redoc`) respond; other native surfaces return 404. The standalone emulator fleet
runs with this flag on. Verified: `openblade/api/main.py` scope middleware; `openblade/config.py:scalar_api_only`.

## Backends (verified) — one of three

Selected by `OPENBLADE_BACKEND` + (`OPENBLADE_ROBOTICS_TRANSPORT`):

| Mode | Robotics | Data path | Use |
|---|---|---|---|
| `mock` / `simulator` (default) | in-memory Mock library | in-memory Mock LTFS (real bytes + sha256) | dev, tests, emulator |
| `real` + transport `scsi` | `mtx` over `/dev/smc*` | host LTFS over `/dev/st*` | direct-attached i3 |
| `real` + transport `webservices` | AML Web Services client (`moveMedium`) | host LTFS | networked real i3 control plane |

See [components/backends.md](components/backends.md). Real hardware is guarded (see [safety gates](configuration/safety-gates.md)).

## Runtime surfaces & ports (verified)

| Surface | Port | Started by |
|---|---:|---|
| API (FastAPI) | 8000 | `uvicorn openblade.api.main:app` (Dockerfile:31, `make dev-backend`) |
| React web UI | 5173→80 | `docker-compose` `web` (nginx serving vite build) |
| Flask NAS UI | 8080 (container) | `gunicorn openblade.web_flask.app:app` (Dockerfile.web:17) |
| Emulator fleet ×3 | 8010/8011/8012 | `make emulator-up` / `make fleet-up` |
| Emulator UI | 5174→8080 | standalone compose `emulator-ui` (nginx) |

## Persistence (verified)

- **SQLite catalog** via SQLAlchemy 2.0 async + aiosqlite. Schema created imperatively
  (`init_db()` → `Base.metadata.create_all`, `openblade/catalog/db.py:274`). **No Alembic
  migrations** (dependency declared but unused). DB at `OPENBLADE_DB_URL`
  (default `sqlite:///~/.openblade/openblade.db`; containers `/data/openblade.db`).
- **AML emulator state, audit log, login activity, latency metrics** live in an
  in-memory `AMLState` singleton and are **not durable** across restarts
  (`openblade/api/aml_state.py`). See [observability gap report](operations/observability-gap-report.md).

## Not present (verified absences — important for the agent)

- No message broker/queue service (jobs run via an **in-process** `JobQueue`+`Worker`).
- No cache/search/object-store service.
- **No distributed tracing** (no OpenTelemetry).
- No durable audit log; no structured/JSON logging or request/correlation IDs
  (structlog runs with default config — `openblade/bootstrap.py:33`).

These absences constrain what the agent can detect and how; they are the primary
input to the [observability gap report](operations/observability-gap-report.md).
