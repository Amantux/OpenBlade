# Volume groups & pools

A **volume group** is the unit you archive into. This page says exactly what one
is, how a tape gets picked for a job, and what happens when a tape fills up —
read from `openblade/domain`, `openblade/catalog` and `openblade/jobs`, not from
the older design docs, which are wrong in several places noted below.

---

## What a volume group is

Three columns. That is the whole thing:

| Column | Notes |
|---|---|
| `id` | UUID |
| `name` | **unique**, indexed — this is the handle everything uses |
| `created_at` | |

Plus two relationships: the cartridges assigned to it, and the file records
archived into it. `barcodes` is a derived property, not a column.

There is **no capacity, no quota, no policy, no library binding, no state** on a
volume group. Capacity lives on the cartridge. Policy lives elsewhere. If a doc
tells you a volume group has a `shard_width` or a list of `lanes`, it is
describing a design that was never built.

A cartridge's `volume_group_id` is nullable — a tape with no group is
"unassigned", which is a normal and expected state.

A file record's `volume_group_id` is **not** nullable. Every catalogued file
belongs to exactly one group.

### The group name is baked into every catalog path

Archive prefixes every catalog path with the group name:

```
source /data/reports/q3.csv  archived into  demo-vg
  ->  catalog path  /demo-vg/reports/q3.csv
```

Since catalog paths are globally unique, archiving the same source into two
groups produces two independent records. And **renaming a group is not possible**
— there is no rename endpoint, and a rename would orphan every path anyway.

---

## "Pool" is a different thing — three of them, in fact

Do not treat "pool" as a synonym for volume group.

1. **`NasPool`** — a NAS-layer object with a `volume_group_ids` field. The
   hierarchy is *pool → list of volume-group ids → cartridges*. That field is a
   **JSON list of strings, not a foreign key**: nothing checks that the groups
   exist, and deleting a group does not update any pool.
   CRUD at `/nas/pools` (and `/storage/nas/pools` — the router is mounted twice).
2. **AML media pools** — `default`, `cleaning`, `pool-critical`, `pool-general`,
   `pool-cold`, seeded inside the Quantum emulator state. These belong to the
   emulator's wire contract and are **completely independent** of volume groups.
   Assigning a barcode to a volume group does not touch them.
3. **`StoragePolicy.pool`** — a plain string id carried into the dry-run planner.

---

## How a tape is chosen for an archive

`_choose_tape()` runs **once per file**, with that file's size. It is a two-phase
first-fit:

**Phase 1 — tapes already in the group.** List the group's cartridges, excluding
any in state `exported`, skip `CLN*` cleaning tapes, and return the **first** one
with `remaining_capacity >= file_size`.

**Phase 2 — recruit an unassigned tape.** If nothing in the group fits, walk
every barcode visible in the library (slots + drives), skip `CLN*`, skip tapes
belonging to a *different* group, skip `exported`, skip ones too small — and the
first survivor is **assigned to this volume group** and used.

Failure: `NoScratchMediaError`, HTTP **503**.

### Consequences you need to know

- **Ordering is ascending barcode string order, not free space.** Both phases
  iterate `ORDER BY barcode`. `AAAA0001` is filled completely before `AAAA0002`
  is touched. This is not load balancing and does not try to be.
- **Archiving into an empty volume group still works, and silently grows it.**
  Verified: `openblade volume-group demo-vg` created a group with zero barcodes;
  `openblade archive --volume-group demo-vg …` completed, and the group
  afterwards contained `MCK00001`. Phase 2 claimed it.
- **A typo in the group name creates a new group.** `run_archive_job` creates the
  group if it does not exist. There is no "unknown volume group" error.
- **Formatting is not checked.** `_choose_tape` never looks at the `formatted`
  flag. An unformatted tape can be selected and assigned to the group; the job
  then fails later, at mount time, with `FormatRequiresConfirmationError`.
- **`NoScratchMediaError` does not always mean "no media".** The same exception
  class is raised for *"no available drives for archive load"* and for
  *"barcode not found in inventory"*. A 503 saying "No scratch media with
  sufficient capacity is available" is the capacity case; read the detail string.

---

## Spillover

**There is no automatic mid-file continuation onto a second tape. A single file
is never split across tapes by the archive engine.**

What *does* happen, and is easy to mistake for spillover:

- **Per-file rollover.** Because `_choose_tape` runs per file, when file N+1 does
  not fit, the job finalises the current tape (unmount → mark instances archived
  → unload → update `used_bytes`), then loads the next tape and carries on. So a
  multi-file dataset genuinely does span tapes, one file at a time.
- **A single file larger than any tape's remaining capacity** simply raises
  `NoScratchMediaError`. It is not split, and there is no retry onto another
  tape.

`StoragePolicy.allow_spillover` exists as a field and is **never read by any
code**. Setting it changes nothing. `TapeAssignment.is_spillover` is planner
output only and has no side effects.

If you need one large object across several tapes, that is *sharding*, which is
a different mechanism with a different entry point — see
[archiving](archiving.md).

---

## Assigning tapes to a group

Four paths, three of them implicit:

| Path | How |
|---|---|
| Explicit API | `POST /volume-groups/{name}/assign` with `{"barcode": "…"}` |
| Implicit | archive phase-2 recruitment (above) |
| Implicit | sharded archive force-assigns every `lane_barcode` |
| Seeding | the demo bootstrap creates `project-alpha`, `media-archive-2024`, `backup-set-a` |

The CLI has **create only** — `openblade volume-group <name>`. There is no CLI
assign command.

The assign endpoint has no validation and will steal a cartridge from another
group with a `200`. See [inventory & barcodes](inventory-and-barcodes.md).

### What you cannot do at all

There is no endpoint, CLI command or repository method to:

- delete a volume group
- rename a volume group
- unassign a cartridge, or move one between groups as an explicit operation
- list a group's file records

Only create and assign exist.

---

## Capacity constants

| Constant | Value | Where |
|---|---|---|
| Cartridge / LTFS default capacity | 12 GB (`12_000_000_000`) | catalog model, mock **and** real LTFS backends |
| Planner default tape capacity | 12 **TB** (`12_000_000_000_000`) | `nas/planner.py` |
| Planner capacity warning | 80 % of a single tape | critical-sequential mode |
| Planner auto-shard threshold | 85 % | balanced mode |

The 12 GB / 12 TB mismatch is real and unreconciled. There is **no environment
variable or config field** for allocation behaviour or tape capacity — capacity
is a constructor argument on the backend only.

---

## The planner is not the executor

`ArchivePlanner` produces multi-tape assignments, spillover flags, warnings and
time estimates. It is a pure dry-run with no side effects, it uses a completely
different rule from `_choose_tape` (scratch-last ordering, directory grouping,
80/85 % thresholds), and it considers **every** library tape regardless of volume
group.

The two can and do disagree. Treat planner output as an estimate, never as a
prediction of which tapes the job will actually use.

---

## Related

- [Archiving](archiving.md) — including sharding across tapes
- [Inventory & barcodes](inventory-and-barcodes.md)
- [The catalog](the-catalog.md)
- [API reference](../reference/api.md) — `volume-groups` tag
