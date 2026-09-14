---
title: Service Map
document_type: architecture
status: verified
last_verified: 2026-07-19
verified_against: [docker-compose.yml, deploy/emulator/docker-compose.standalone.yml, Dockerfile.web]
owners: [platform]
tags: [architecture, services]
---

# Service Map

```mermaid
flowchart TD
  web[web React :5173->80] --> api
  flask[web_flask :8080] -->|OPENBLADE_WEB_BACKEND_URL| api[api :8000]
  api --> catalog[(SQLite /data/openblade.db)]
  api -->|/health probe| e1[emulator-library-1 :8010]
  api --> e2[emulator-library-2 :8011]
  api --> e3[emulator-library-3 :8012]
  ui[emulator-ui :5174] --> e1 & e2 & e3
```

## Dependencies (verified)
- `web`/`web_flask`/`cli` → `api` (HTTP). `api` → `catalog` (SQLite) and the active backend.
- `api` → `emulator` fleet only via HTTP `/health` probe + AML calls (`OPENBLADE_EMULATOR_URLS`).
- No message broker, cache, or search service. Jobs run in-process.

See [deployment topology](deployment-topology.md) and [components/api](../components/api.md).
