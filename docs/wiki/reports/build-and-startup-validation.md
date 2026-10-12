---
title: Build and Startup Validation
document_type: report
status: verified
last_verified: 2026-07-19
verified_against: [scripts/validate_runtime.py, Dockerfile, Makefile]
owners: [platform]
tags: [build, startup, validation]
---

# Build and Startup Validation

## Verified locally this session (in-process, `scripts/validate_runtime.py`)

| Step | Result |
|---|---|
| App boots (`create_context` seeding) | ok (first request ~4–5s cold) |
| `GET /healthz` | 200, status `ok`, all components ok |
| `GET /readyz` | 200, `ready=true` |
| Auth success `POST /aml/users/login` | 200 |
| Auth failure (bad creds) | 401 (AUTH-001 evidence) |
| Inventory `GET /aml/physicalLibrary/elements` | 200, 50 slots |
| Prometheus metrics endpoint | 200, **14 metric families** |
| Core workflow: sharded archive→restore | byte-exact, checksum verified |
| Restart-persistence | catalog persists; `aml_state` resets (expected, ADR-0003) |

**8/8 checks passed.** Reproduce: `python3 scripts/validate_runtime.py` (needs `.[dev]`).

## Build/startup paths (verified to exist; container paths = verify-in-CI)

| Path | Command | Status |
|---|---|---|
| Native API | `uvicorn openblade.api.main:app --port 8000` (`make dev-backend`) | verified (in-process) |
| Native tests | `make test` / `pytest` | verified (executed) |
| Frontend | `cd frontend && npm ci && npm run build` | verified earlier (build ok) |
| docker-compose | `make up` (api+web) | verify-in-CI (not spun up here to avoid port impact) |
| Emulator fleet | `make emulator-up` / `make fleet-up` | verify-in-CI |
| Flask UI | `gunicorn openblade.web_flask.app:app` | verify-in-CI (web-flask-smoke) |

## Not validated here (documented)
Container/compose runtime and the Flask/emulator container startup were **not** started
in this environment (to avoid impacting other running processes/ports). CI validates
container builds (`docker compose build web`, emulator compliance). See
[remaining work](remaining-work.md).
