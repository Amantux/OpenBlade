---
title: Commands (canonical)
document_type: development
status: verified
last_verified: 2026-07-19
verified_against: [Makefile, pyproject.toml, frontend/package.json, scripts/]
owners: [platform]
tags: [development, commands]
---

# Canonical Commands

Use the repo's existing frameworks (Make, pytest, npm). All verified against the Makefile.

| Intent | Command |
|---|---|
| setup (dev) | `pip install -e '.[dev]'` (or `uv pip install -e '.[dev]'`) |
| build (frontend) | `make build-frontend` (`cd frontend && npm run build`) |
| start (api, dev) | `make dev-backend` (`uvicorn openblade.api.main:app --reload --port 8000`) |
| start (frontend) | `make dev-frontend` |
| up (compose) | `make up` / down: `make down` / logs: `make logs` |
| emulator fleet | `make emulator-up` / `make fleet-up` |
| test | `make test` (`pytest tests/unit tests/safety tests/integration`) |
| test (unit) | `make test-unit` |
| lint | `make lint` (`ruff check . && ruff format --check .`) |
| typecheck | `mypy openblade` |
| seed | `make seed-libraries` / `make seed-tapes` / `make seed-all` |
| clean | `make clean` |
| health | `curl -s localhost:8000/healthz` · `curl -s localhost:8000/readyz` |
| validate (runtime) | `python3 scripts/validate_runtime.py` |
| docs-check | `python3 scripts/wiki_validate.py` |
| agent-policy-check | `pytest tests/unit/test_agent_policy.py` |

> No `alembic`/migration command exists (schema via `create_all`). No `reset` target
> beyond `make clean` + a fresh DB; reset = delete the SQLite file at `OPENBLADE_DB_URL`.
