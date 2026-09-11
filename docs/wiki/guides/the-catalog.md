# The catalog

The catalog is a single SQLite file. It is the only thing that turns "bytes on a
tape" into "a file you can restore by path". **If you lose it, most of your
archive becomes unaddressable.** This page says where it lives, what is in it,
what can be rebuilt from tape (little), and what cannot (most of it).

---

## Where the file lives

| Context | Path | Set by |
|---|---|---|
| API / server | `~/.openblade/openblade.db` | `OPENBLADE_DB_URL` (a full SQLAlchemy URL, not a path) |
| CLI | `~/.openblade/openblade.db` | **hardcoded** |
| Docker | `/data/openblade.db` on the `openblade-data` volume | `OPENBLADE_DB_URL` in compose |

> ⚠️ **The CLI ignores `OPENBLADE_DB_URL`.** It builds its config by hand instead
> of going through the config loader. So if you set `OPENBLADE_DB_URL` for the
> server and then run `openblade jobs`, you are reading a *different database*
> and it will look empty. This is a defect, not a design.

Two more traps in the container images:

- `OPENBLADE_DB_PATH` is set in the Dockerfiles and **read by no code**. Setting
  it does nothing.
- `OPENBLADE_BACKEND=simulator` is set in some images. `simulator` is not a valid
  value; it falls back to `mock`. Use `mock` if you set it at all.

The parent directory is created automatically. `sqlite+aiosqlite:///` URLs are
normalised to `sqlite:///`.

---

## What is stored

Roughly 25 tables. The ones an operator needs to understand:

### The archive catalog — this is what restores depend on

| Table | Contents |
|---|---|
| `volume_groups` | id, unique name, created_at. Three columns, nothing more. |
| `cartridges` | barcode (unique), volume group, library, capacity/used bytes, state, `formatted` flag |
| `file_records` | **logical catalog path (unique)**, size, SHA-256, volume group, and sharding columns (`shard_count`, `shard_index`, `block_size`, `shard_profile`, `parent_id`) |
| `file_instances` | **one physical copy of one file on one tape**: barcode, tape path, state, archived_at, checksum_verified |

`file_instances` is the row that makes a restore possible. A `file_record` with
no `archived` instance is not restorable. Shards are modelled as child
`file_records` linked to the parent by `parent_id`, each with its own instance.

### Operational

| Table | Contents |
|---|---|
| `jobs` | job type, state, metadata JSON, error, timestamps |
| `safety_tokens` | token, operation, target barcode, expiry — see [formatting tapes](formatting-tapes.md) |
| `tape_op_log` | audit of every orchestrated tape operation: type, barcode, drive, slot, result, error |
| `library_instances` | configured libraries / emulators |

### Auth and RBAC

`aml_users`, `rbac_roles`, `rbac_users`, `rbac_api_tokens`, `rbac_audit_events`.

### The NAS side — a *second, independent* catalog

`nas_storage_policies`, `nas_cache_drives`, `nas_configs`, `nas_shares`,
`nas_pools`, `nas_datasets`, `nas_file_records`, `nas_restore_jobs`,
`path_mappings`, `catalog_rebuild_runs`, `manifest_versions`.

> **`file_records`/`file_instances` and `nas_file_records`/`path_mappings` are
> two separate catalogs that do not share data.** The classic archive/restore
> path writes the first. NAS ingest and hydration write the second. This matters
> enormously for recovery — read on.

`docs/architecture.md` describes the catalog as "volume groups, file records,
file instances, and safety tokens". That is four tables out of about
twenty-five.

---

## Rebuild from tape — and its limits

There is a rebuild facility, driven from:

```
POST /nas/catalog/rebuild/plan
POST /nas/catalog/rebuild/activate
POST /nas/catalog/rebuild/{run_id}/execute
GET  /nas/catalog/rebuild/runs
GET  /nas/catalog/rebuild/loaded-tapes
```

The NAS router is mounted twice, so every one of these is also reachable under
a `/storage` prefix (`/storage/nas/catalog/rebuild/plan`, …). Both spellings work.

There is **no CLI command** for it.

It reads three sidecar files per tape — `/.openblade/tape.json`,
`/.openblade/manifest.json`, `/.openblade/catalog-shard.json` — and classifies
each barcode as scannable, missing-manifest, missing-shard or invalid. It refuses
to enqueue unless there are zero invalid tapes and at least one scannable one.

### What it recovers

`cartridges` rows · `nas_datasets` (forced to `ARCHIVED`) · `nas_file_records`
(forced to `OFFLINE_ON_TAPE`, `cache_path` cleared) · `path_mappings` ·
`manifest_versions`.

### What it does **not** recover

- **`file_records`, `file_instances` and `volume_groups`** — the entire classic
  archive catalog. Nothing in the rebuilder touches them. **Anything archived
  with `openblade archive` or `POST /archive/` is not recoverable by this tool.**
- `jobs` and `tape_op_log` history.
- All RBAC and auth tables, including API tokens.
- `nas_pools`, `nas_storage_policies`, `nas_shares`, `nas_cache_drives`,
  `nas_configs` — configuration is not written to tape.
- Dataset `name`, `source_path`, `source_host` and `shard_map`, unless a row
  already exists. On a truly empty database the dataset name falls back to its id.
- Per-file `tape_offset` and `cache_path`.
- Any tape whose `catalog-shard.json` is missing — skipped or failed.

`docs/disaster-recovery.md` lists LTFS tape content as recoverable by "re-index
from media". That is true for NAS datasets with on-tape sidecars. It is **not**
true for the classic catalog, which has no re-index path at all.

---

## Backup implications

Lost with the SQLite file, and **not** recoverable from tape:

1. The classic archive catalog — every file archived via the CLI or `/archive/`
   becomes unlocatable by path.
2. All users, roles, API tokens, audit events.
3. All NAS configuration: pools, policies, shares, cache drives.
4. Job history and the tape operation audit log.

Partially recoverable, and only for tapes carrying the `/.openblade/*.json`
sidecars: NAS datasets, NAS file records, path mappings, manifest versions,
cartridge rows.

### Back it up

```bash
python scripts/backup_db.py --db "$OPENBLADE_DB_URL"     # WAL-safe, prints sha256
python scripts/restore_verify.py                          # verify a backup
```

Procedure in `docs/disaster-recovery.md`.

Practical advice:

- Back up the catalog on the **same schedule as your archives**, not less often.
  A catalog older than your last archive silently loses those files.
- Keep the printed SHA-256. A silently corrupt catalog backup is worse than none.
- If you use Docker, the catalog is on the `openblade-data` volume. Back up the
  volume, not the container.
- The repo contains a checked-in `openblade.db` at its root. That is a fixture,
  not your data. Nothing in normal startup reads it.

---

## Browsing it

```bash
openblade catalog /
openblade catalog /demo-vg
```

```
               Catalog /demo-vg
┏━━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Name     ┃ Type ┃ Size ┃ Path              ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ note.txt │ file │ 16   │ /demo-vg/note.txt │
└──────────┴──────┴──────┴───────────────────┘
```

Over HTTP: `GET /catalog/`, `GET /catalog/{file_id}/shards`.

For files written by the **simple** archive engine the path shape is
`/<volume-group-name>/<path relative to the archived source root>`, and the group
name is part of the identity of every file — which is why there is no rename.

⚠️ Files written by the **sharded** engine are catalogued under their raw
absolute source path instead, with no group prefix. Two path conventions coexist
in one table; see [restoring](restoring.md).

---

## Related

- [Volume groups & pools](volume-groups-and-pools.md)
- [Restoring](restoring.md)
- [FUSE & NAS namespace](fuse-and-nas.md) — the second catalog
- `docs/disaster-recovery.md`
