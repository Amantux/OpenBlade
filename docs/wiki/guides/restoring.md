# Restoring

Restore is addressed by **catalog path**, not by barcode. The catalog tells
OpenBlade which tape(s) hold the bytes; you never name a tape.

---

## Full restore of one file

```bash
openblade restore --path /demo-vg/note.txt --to /tmp/out/note.txt
```

```json
{"job_id": "4e4930fc-…", "status": "completed", "job_type": "restore"}
```

```bash
cat /tmp/out/note.txt
hello openblade
```

Over HTTP:

```
POST /restore/   {"catalog_path": "/demo-vg/note.txt", "dest_path": "/tmp/out/note.txt"}
```

(The request model also accepts `source_path`; `catalog_path` + `dest_path` is
the pair that matches the CLI's `--path` / `--to`.)

Both were run against the simulator while writing this page.

Find the path first if you do not know it:

```bash
openblade catalog /
openblade catalog /demo-vg
```

Remember the path shape: `/<volume-group-name>/<path relative to the archived
source root>`.

### Unknown path

```
FileNotFoundError: Catalog path /demo-vg/nope.txt not found
```

Over HTTP that is a **404**. A cartridge in state `exported` gives
`CartridgeOfflineError` → **409**.

---

## Selective restore

There is no "restore this subtree" or "restore matching glob" operation. The
granularity is **one catalog path per job**. To restore a directory, enumerate it
with `openblade catalog <dir>` (or `GET /catalog/`) and issue one restore per
file.

Two things make that less painful than it sounds:

- Restore mounts **read-only**, so it cannot damage a tape.
- Files archived together are usually adjacent on the same tape, so sequential
  restores of one directory do not thrash the changer.

The NAS layer has a richer restore-job model (queued / planning / running /
paused / completed / failed / cancelled, with `partial_success`, and operator
`cancel` / `pause` / `resume` / `retry`). That is a separate subsystem over the
NAS catalog, not over `file_records`. See [FUSE & NAS namespace](fuse-and-nas.md).

---

## Sharded restore

`POST /restore/` **auto-detects** sharding — if the record has child shard
records, or its `shard_count` is greater than 1, it takes the sharded path. There
is no `/restore/sharded` endpoint and no flag to set.

> ⚠️ **`openblade restore` does not do this detection.** The CLI calls the
> non-sharded path unconditionally. Restoring a block-striped file from the CLI
> takes the wrong route. Use the API for anything sharded.

How the sharded path works:

1. Look up the parent record by catalog path.
2. List its shard children (`parent_id`), ordered by `shard_index`.
3. **Integrity gate:** if the record claims `shard_count > 1` or profile
   `block_stripe`, the number of child records must match exactly, else the job
   fails with `Missing shard catalog entries` → **404**. Likewise if any shard has
   no `archived` instance: `Missing archived shard instances`.
4. Acquire **all** shard tapes at once. There is no sequential fallback — if the
   library cannot mount N tapes in N drives simultaneously, the restore cannot
   run.
5. Read every shard in parallel into a scratch directory.
6. Reassemble by round-robining `block_size` reads across the shard files,
   streaming straight to the destination.

The block size is read back **from the catalog**, so you do not have to remember
what you archived with.

---

## Checksum verification

SHA-256 throughout. There is no other digest.

| Path | What is compared | On mismatch |
|---|---|---|
| Single instance | recomputed digest vs `file_record.checksum_sha256` | temp file renamed to `quarantine_<name>` in the scratch dir, job `failed` |
| Sharded | digest computed **during reassembly** vs the parent's whole-file checksum | **destination file is deleted**, job `failed`, `ChecksumMismatchError` |

Error text you will actually see:

```
Reassembled checksum mismatch: <actual> != <expected>
Checksum mismatch: expected <expected>, got <actual>
```

Two honest caveats:

- **The "quarantine" is not durable.** The scratch directory is removed in the
  same `finally` block, so the quarantined file is deleted moments later. Do not
  promise operators a recoverable artifact — treat a checksum failure as "nothing
  was produced".
- **Per-shard checksums are stored but not re-verified on restore.** Only the
  reassembled whole-file digest is checked. That catches corruption, but it does
  not tell you *which* shard was bad.

A checksum mismatch on a restore is a serious signal: it means the tape, the
drive, or the catalog disagrees with what was written. Do not retry blindly —
check drive health and read `docs/runbooks/replace-failed-drive.md`.

---

## Where restored bytes land

The classic restore path writes exactly where you point `--to` / `destination`.

The NAS hydration path is different: it writes to `$OPENBLADE_RESTORE_DIR/{file_id}`,
defaulting to `/tmp/openblade-restore` — note that this reads the environment
variable directly with a **different default** from `config.restore_dir`
(`~/.openblade/restore`). Two defaults for one concept; be explicit about the
variable if it matters.

---

## Retry

There is none for classic restore jobs. A failure sets state `failed` and
re-raises. Re-issue the command.

NAS restore jobs do have an operator-triggered `retry` (only from status
`FAILED`), plus `cancel`, `pause` and `resume`. Nothing retries automatically
anywhere.

---

## Related

- [Archiving](archiving.md)
- [The catalog](the-catalog.md) — what makes a restore possible
- [Jobs & monitoring](jobs-and-monitoring.md)
- [Troubleshooting](troubleshooting.md)
