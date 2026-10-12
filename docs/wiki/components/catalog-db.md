---
title: Component — catalog (SQLite)
document_type: component
component: catalog
status: verified
last_verified: 2026-07-19
verified_against: [openblade/catalog/db.py:274, openblade/config.py:34, pyproject.toml]
owners: [platform]
tags: [component, database]
---

# Component: `catalog`

SQLite metadata store via SQLAlchemy 2.0 async + aiosqlite.

| Property | Value |
|---|---|
| Schema | imperative `Base.metadata.create_all` (`db.py:274`) — **no Alembic** (dep declared, unused) |
| Location | `OPENBLADE_DB_URL` (default `sqlite:///~/.openblade/openblade.db`; container `/data/openblade.db`) |
| Health | `/healthz` DB component probes `datasets`,`path_mappings`,`cartridges`,`rebuild_runs` |
| Backup | file-copy of the SQLite DB (no built-in tooling) |

Failure: [DB-001](../operations/failure-taxonomy.yaml) / [DISK-001](../operations/failure-taxonomy.yaml). Runbooks: [RB-DB-001](../runbooks/database-unavailable.md), [RB-DISK-001](../runbooks/disk-pressure.md).

> Schema changes: because there are no migrations, schema evolution happens via
> `create_all` (adds tables/columns for new models only). Data-migrating changes are
> a **human, out-of-band** task — the agent must never run migrations (none exist).
