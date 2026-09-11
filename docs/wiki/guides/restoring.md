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

### ⚠️ The path shape depends on which engine archived it

| Archived by | Catalog path |
|---|---|
| Simple archive (`openblade archive`, `POST /archive/`) | `/<volume-group-name>/<path relative to the source root>` |
| **Sharded archive** (`POST /archive/sharded`) | **the raw absolute source path**, e.g. `/data/big/note.txt` — no volume-group prefix at all |

Verified: a sharded archive of `…/src` into `shard-vg` produced the record
`/tmp/…/src/note.txt`. The shard children are that path plus `#shardNNNN`.

So the "everything is under `/<group>/`" rule is **not** an invariant. If a
restore of a sharded file 404s with a group-prefixed path, list the catalog and
use the absolute path you see there.

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
   run. Two different failure shapes here: asking for **more shards than the
   library has drives** fails immediately with `DriveBusyError`, but if the
   drives merely happen to be *busy*, the request **blocks for the 300-second
   default timeout** before raising the same error. A five-minute stall is that
   timeout, not a deadlock.
5. Read every shard in parallel into a scratch directory.
6. Reassemble by round-robining `block_size` reads across the shard files,
   streaming straight to the destination.

The block size is read back **from the catalog**, so you do not have to remember
what you archived with.

---

## Checksum verification

SHA-256 throughout. There is no other digest.

There are three distinct mismatch behaviours. **They differ in whether you are
left with a file on disk**, so read the row that matches the path you used.

| Path | What is compared | On mismatch |
|---|---|---|
| **Classic restore** (`openblade restore`, and the default `POST /restore/` branch) | recomputed digest vs `file_record.checksum_sha256` | the output is renamed to **`<your-destination>.quarantine`, in your destination directory, and is left there permanently**; job `failed` |
| **Sharded, per-instance** | same | temp file renamed to `quarantine_<name>` inside the scratch dir, which is then deleted — nothing survives |
| **Sharded, reassembled** | digest computed during reassembly vs the parent's whole-file checksum | **destination file is deleted**; job `failed`, `ChecksumMismatchError` |

Error text you will actually see:

```
Checksum mismatch for <catalog_path>                       # classic
Checksum mismatch: expected <expected>, got <actual>       # sharded, per-instance
Reassembled checksum mismatch: <actual> != <expected>      # sharded, reassembly
```

> ⚠️ **After a failed classic restore, clean up the `.quarantine` file
> yourself.** Nothing deletes it. It is corrupt data sitting next to where good
> data was supposed to go, and on a retry into the same directory it is easy to
> mistake for output. Check for it before re-running.

Two further caveats:

- **Only the classic path leaves you an artifact.** For both sharded paths,
  treat a checksum failure as "nothing was produced".
- **Per-shard checksums are stored but not re-verified during reassembly.** Only
  the reassembled whole-file digest is checked. That catches corruption, but it
  does not tell you *which* shard was bad.

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
