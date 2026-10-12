---
title: Deployment Topology
document_type: architecture
status: verified
last_verified: 2026-07-19
verified_against: [docker-compose.yml, deploy/emulator/docker-compose.standalone.yml, Dockerfile, Dockerfile.web, frontend/Dockerfile]
owners: [platform]
tags: [architecture, deployment, docker]
---

# Deployment Topology

## Primary stack — `docker-compose.yml` (verified)
- **api**: build `Dockerfile` (python:3.12-slim, non-root, `mtx`+`gosu`); env `OPENBLADE_BACKEND=mock`, `OPENBLADE_DB_URL=sqlite:////data/openblade.db`; volume `openblade-data:/data`; ports `8000:8000`; healthcheck `curl /health` (10s/5s/×5); `extra_hosts host.docker.internal:host-gateway`.
- **web**: build `frontend/Dockerfile` (node build → nginx); ports `5173:80`; `depends_on api (service_healthy)`.

> Config note (verified drift): compose api uses backend `mock` while `Dockerfile` default is `simulator`. The **Flask** UI (`Dockerfile.web`, gunicorn :8080) is **not** in this compose — the compose `web` is the React app.

## Emulator fleet — `deploy/emulator/docker-compose.standalone.yml` (verified)
- 3 × emulator (`Dockerfile.local`, `OPENBLADE_SCALAR_API_ONLY=true`, profile `scalar-i3-50-3`) on `8010/8011/8012`, per-lib data volumes; `emulator-ui` (nginx) on `5174:8080`. Start via `make emulator-up` / `make fleet-up`.

## Startup commands (verified)
| Surface | Command |
|---|---|
| api | `uvicorn openblade.api.main:app --host 0.0.0.0 --port 8000` |
| web (dev) | `cd frontend && npm run dev` (vite) |
| web_flask | `gunicorn -k gevent -w 1 -b 0.0.0.0:8080 openblade.web_flask.app:app` |
| emulator | `uvicorn openblade.api.main:app --port 8010` |
