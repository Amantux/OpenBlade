# Archiving

Two archive engines exist. The simple one writes files to one tape at a time and
is reachable from the CLI. The sharded one writes across several tapes and
drives in parallel and is **API-only**. This page covers both, and is written
against the code — `docs/sharding.md` and `docs/diagrams/shard-design.md`
describe several behaviours that were never implemented, listed at the end.

---

## 1. Simple archive

```bash
openblade archive --volume-group demo-vg --path /data/reports
```

```json
{"job_id": "d0398cd5-…", "status": "completed", "job_type": "archive"}
```

or

```
POST /archive/   {"source_path": "/data/reports", "volume_group": "demo-vg"}
```

What it does, per file, in source order:

1. Pick a tape — see [volume groups & pools](volume-groups-and-pools.md). Tape
   selection is per file, first-fit in barcode order.
2. Load it into a free drive if it is not already loaded; mount `READ_WRITE`.
3. Write the file; record a `file_record` (logical path, size, SHA-256) and a
   `file_instance` (barcode + tape path), initially `pending`.
4. When the next file needs a different tape: unmount, flip that tape's pending
   instances to `archived`, unload, update `used_bytes`, commit.

Catalog path = `/<volume-group-name>/<path relative to the source root>`. The
source directory's own name is not included.

### It is synchronous

`openblade archive` blocks until the whole job finishes. `POST /archive/`
likewise runs the job **inside the request handler**, then returns
`202 Accepted` with `"status": "pending"` — which is a hardcoded literal, not the
real state. Verified: a `202 … "pending"` response was followed immediately by
`GET /jobs/` showing that job already `completed`.

Practical consequences: expect long-running HTTP requests, set generous proxy
timeouts, and poll `GET /jobs/{id}` for the truth.

All archive requests are serialised by a single process-wide lock, so two
concurrent archives queue rather than fight over drives.

### Failure behaviour

There is **no retry**. On any exception the job is marked `failed` with the
exception text, the drive is best-effort unmounted and unloaded, and catalog
rows for files that never reached tape are deleted. Tapes already finalised
earlier in the run keep their archived files.

`dry_run=True` on the core engine returns `tapes_used: []` and marks the job
completed **without consulting any tape**. It is not a plan; for a real plan use
the planner endpoints.

---

## 2. Sharded archive

Sharding writes one dataset across N tapes in N drives simultaneously. It is
reachable **only** through:

```
POST /archive/sharded
{
  "source_path": "/data/big",
  "volume_group": "shard-vg",
  "lane_barcodes": ["VOL001L9", "VOL002L9"],
  "mode": "stripe",
  "block_size_mb": 128
}
```

`grep -rn "shard" openblade/cli/` returns nothing. **The CLI cannot create
sharded archives, and `openblade restore` cannot correctly restore them** — it
does not replicate the shard detection that the API does.

### The number of lanes is what *you* pass

This is the single most misunderstood part. Shard count is **`len(lane_barcodes)`**.
It is never derived from the file size and never derived from the drive count.
If you pass more lane barcodes than the library has drives, the drive scheduler
rejects the job immediately with `DriveBusyError` — it does not degrade to fewer
lanes.

`mode: "block_stripe"` with fewer than two lanes is rejected with **422**:

```json
{"detail": "block_stripe mode requires at least 2 lane_barcodes"}
```

Verified against the simulator.

### `stripe` — whole files across lanes

- Files are collected with `rglob("*")` and sorted.
- Lane assignment is **positional round-robin over that sorted list**:
  `lane = index % lane_count`. Nothing is split; each file is one shard.
- Files are processed in batches of exactly `lane_count`; one batch = one
  parallel write round = one atomic commit unit.
- Tape path: `/stripe/<filename>`.

Because assignment is positional, a file's lane **changes** if files are added to
or removed from the source directory. Re-archiving is not lane-stable.

### `block_stripe` — one file's bytes across lanes

- The whole-file SHA-256 is computed up front.
- The file is cut into `block_size` blocks (default **128 MiB**) and blocks are
  assigned round-robin: `lane = block_index % lane_count`.
- **One shard per lane**, not one per block: each shard is the concatenation of
  all blocks belonging to that lane.
- Tape path: `/block_stripe/<group-uuid>/<filename>.shardNNNN`.

Two operational costs to budget for:

- Each shard is first written to a **temporary full copy on local disk**, under
  `./.openblade-scratch/archive-<uuid>` — *relative to the process working
  directory*. You need roughly 1× the source file in free local space.
- Each of the N workers **re-reads the entire source file** to pick out its own
  blocks. Throughput will not approach N× a single drive's rate.

### Commit is atomic per unit

The protocol, in order:

1. Write every shard/file in the unit, and checksum-verify each one.
2. Create catalog rows — instances start as `pending`.
3. Unmount every tape and unload every drive, **aggregating all failures**.
4. Only now flip the instances to `archived`.

If step 3 fails, the data is on tape but the commit does not happen, and the job
error reads:

```
shards written but physical state is unknown (reconcile required): …
```

That is deliberate: a dirty unmount or a stuck unload blocks the commit even
though the bytes wrote fine.

On mid-archive failure, staged instances stay `pending` forever. They are never
selected for restore, so the catalog never claims data it cannot serve — but
there is **no rollback, no erase, no retry and no repair job**. Bytes may sit on
tape unreferenced.

Commit granularity: `stripe` commits **per batch of `lane_count` files**;
`block_stripe` commits **per file**, and a failure on one file does not stop the
loop. Terminal job state is `completed` if there were no errors, otherwise
`failed_recoverable`.

### Concurrency caveat

A `DriveScheduler` is constructed **per HTTP request**, not shared. Archive is
saved by the process-wide archive lock. Restore has no equivalent lock, so
concurrent sharded restores can each believe they own the same drive.
`docs/sharding.md`'s invariant "DriveScheduler never grants the same drive to two
jobs" holds only within one scheduler instance.

### Checking the result

```
GET /catalog/{file_id}/shards     # the child records for a sharded file
GET /jobs/{job_id}
```

---

## 3. What the existing sharding docs get wrong

`docs/sharding.md` and `docs/diagrams/shard-design.md` predate the
implementation. Do not follow them. The larger divergences:

| Doc claim | Reality |
|---|---|
| Failed shards are marked `failed` | Nothing is ever marked `failed`; instances are abandoned as `pending` |
| Tape paths like `ARCH01L8:/shards/bigfile.tar/shard0000` | `/stripe/<name>` or `/block_stripe/<uuid>/<name>.shardNNNN` |
| One tape file per block | One tape file per **lane** |
| `block_size` default 1 GB | **128 MiB** |
| STRIPE lane = `hash(path) % shard_width`, "stable re-archive" | Positional round-robin; not stable |
| `volume_group.shard_width` / `.lanes` / `.scratch_tapes` / `.add_lane()` | None exist. Lanes are a per-request parameter |
| Automatic mode selection by file size | Mode comes only from the request; the threshold constant is referenced by nothing |
| `N = min(shard_width, available_drives)` | N is exactly `len(lane_barcodes)`; over-request fails |
| Shard manifest columns on `file_instances` (`shard_group_id`, `shard_index`, `block_start`…) | None exist. Shards are child `file_records` linked by `parent_id` |
| `DriveScheduler` issues load/unload | It is a pure in-memory lock table with zero hardware coupling |
| Retry on drive-load failure; repair job for partial shards; fallback to non-sharded restore | None implemented |
| Throughput tables | Unvalidated theoretical models with no benchmark behind them |

`ERASURE` mode is correctly labelled future work and is correctly absent.

`ShardedArchiveResult.shard_group_ids` is populated in stripe mode with a freshly
minted UUID that is **never persisted**. It correlates with nothing — do not use
it as a lookup key.

---

## Related

- [Restoring](restoring.md)
- [Volume groups & pools](volume-groups-and-pools.md)
- [Jobs & monitoring](jobs-and-monitoring.md)
- [The catalog](the-catalog.md)
