# Disaster Recovery — OpenBlade

OpenBlade's durable state is a **single SQLite catalog** (path in
`OPENBLADE_DB_URL`, e.g. `sqlite:////data/openblade.db`). Everything else is
either derivable (LTFS content, library inventory) or in-memory and non-durable
(`AMLState`: emulator state, audit, login, metrics — lost on restart). So DR
centers on the catalog: back it up, and *prove* the backup restores.

## What is and isn't recoverable

| State | Durable? | Recovery |
|---|---|---|
| Catalog (datasets, file records, jobs, cartridges, RBAC) | yes — SQLite file | restore from backup (this doc) |
| LTFS tape content | yes — on tape | re-index from media |
| `AMLState` (emulator/audit/metrics) | **no** — in-memory | rebuilt on boot; metrics history is lost on restart (known SPOF) |

## Back up

WAL-safe online snapshot (safe while the app is running):

```
python scripts/backup_db.py --db "$OPENBLADE_DB_URL" --out backups/openblade-YYYYMMDD.db
```

Prints `{path, bytes, sha256}`. Store the sha256 with the artifact; store backups
off the app host (the app host is itself a SPOF).

## Verify a restore (do this regularly — a backup you haven't restored is a hope)

Runs entirely on an **isolated copy in a temp dir**; it never writes to the source
or production DB:

```
python scripts/restore_verify.py --backup backups/openblade-YYYYMMDD.db --json
```

Checks, in order: backup present → `PRAGMA integrity_check` → expected schema
present → a real read through the app's data layer. Exit 0 = verified; exit 1 =
a check failed (a corrupt or incomplete backup is reported, not crashed).

With no `--backup`, the CLI self-tests the whole backup→restore→verify round-trip
against a freshly-seeded DB — this is what CI runs, with no production data.

## Restore for real (operator procedure)

1. Stop the app (writers must be quiesced).
2. `restore_verify --backup <artifact>` — **must** pass before you trust it.
3. Copy the verified backup to the live `OPENBLADE_DB_URL` path.
4. Start the app; hit `/healthz` (expect db+library+ltfs healthy) and
   `python scripts/verify_topology.py` (expect topology OK).
5. Re-check catalog counts against the last known-good.

## Pending (needs production access — not run in CI)

- A restore drill against a **real production backup** artifact. CI only exercises
  the mechanism on seeded data; never restore production data in a test path.
- Scheduled backup rotation + off-host replication.
