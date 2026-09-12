# Mailslot (I/E) flows and bulk restore — mhvtl rig addendum

> Addendum to [`real-data-campaign.md`](real-data-campaign.md). That runbook's
> **"Does not exist"** table listed *Import / export (I/E slot) flows*, *Bulk /
> directory restore* and *CLI sharded archive* as unimplemented. This records
> the operator surfaces built for those three and what they did on the
> rehearsal rig. The campaign runbook itself is unchanged; read it first.

Branch `feat/cli-restore-shard-mailslot`. Rig: `scripts/mhvtl/setup.sh`,
3 drives / 8 storage slots / **4 import-export elements (9–12)** on `/dev/sg2`.

```bash
eval "$(scripts/mhvtl/env.sh)"
export OPENBLADE_DRIVE_SERIAL_MAP="OBLADE_D01:0,OBLADE_D02:1,OBLADE_D03:2"
```

---

## 1. What was added

| Command | Notes |
|---|---|
| `openblade mailslot list` | Every I/E element and what is in it. |
| `openblade mailslot import <ie-slot> [--to-slot N]` | I/E → storage. Without `--to-slot` it picks the first empty storage slot **and names it** in the output. |
| `openblade mailslot export <barcode> [--ie-slot N] [--force]` | Storage → first empty I/E. **Refuses** when the cartridge or its volume group still carries archived data. |
| `openblade restore tree <catalog-prefix> --dest DIR [--dry-run]` | Every archived file under a prefix, spanning tapes. |
| `openblade restore file <catalog-path> --dest DIR` | One file, using the sharded reassembly path when the catalog says it is sharded. |
| `openblade archive sharded <src> --volume-group G --mode stripe\|block-stripe [--lanes N \| --lane-barcode B …] [--block-size-mb N]` | The CLI half of `POST /archive/sharded`. |

`restore` and `archive` became command *groups*. The bare forms
`openblade restore --path X --to Y` and `openblade archive --volume-group G
--path P` — what `scripts/campaign/` runs — still work unchanged, and there are
regression tests pinning that.

**stdout is the result and nothing else.** Progress lines, warnings and the
chosen-slot narration go to stderr, so `openblade restore tree … | jq` works.

---

## 2. Rig transcript

`git rev-parse --short HEAD` = `7478224`, changer `/dev/sg2`.

### 2.1 list → export → list → import

```
$ openblade mailslot list
{"slotCount": 4, "occupiedCount": 0, "slotIds": [9, 10, 11, 12]}

$ openblade mailslot export OB0007L8          # scratch tape, no archived data
{
  "barcode": "OB0007L8",
  "source": "storage_slot",      "sourceSlot": 6,
  "destination": "import_export_slot", "destinationSlot": 9,
  "destinationSlotChosen": true,
  "exported": {"archivedFilesOnCartridge": 0, "carriesData": false, …}
}
exit=0

$ mtx -f /dev/sg2 status | grep IMPORT
      Storage Element 9 IMPORT/EXPORT:Full :VolumeTag=OB0007L8
      Storage Element 10 IMPORT/EXPORT:Empty
      Storage Element 11 IMPORT/EXPORT:Empty
      Storage Element 12 IMPORT/EXPORT:Empty

$ openblade mailslot list      # .occupied
[{"slotId": 9, "occupied": true, "barcode": "OB0007L8"}]

$ openblade mailslot import 9                  # no --to-slot
{"barcode": "OB0007L8", "sourceSlot": 9,
 "destination": "storage_slot", "destinationSlot": 6,
 "destinationSlotChosen": true}
exit=0

$ mtx -f /dev/sg2 status | grep -E "Element (6|9)[: ]"
      Storage Element 6:Full :VolumeTag=OB0007L8
      Storage Element 9 IMPORT/EXPORT:Empty
```

The robot really moved it: `mtx transfer` both ways, element numbers round-tripped
verbatim (the rig's I/E station is 9–12, i.e. *past* the 8 storage slots).

### 2.2 tree restore, single tape

Archived a 4-file tree (`alpha/same.txt`, `beta/same.txt`, `beta/deep/n.txt`,
`blob.bin` @ 250 kB) into `/rigtree` on `OB0001L8`, then:

```
$ openblade restore tree /rigtree --dest $HOME/out
{
  "catalogPrefix": "/rigtree", "destDir": "/tmp/obrig-final/out",
  "filesRestored": 4, "filesFailed": 0,
  "bytesRestored": 250028, "filesVerified": 4,
  "perTapeCounts": {"OB0001L8": 4}, "tapesUsed": ["OB0001L8"],
  "failures": [], "status": "completed"
}
exit=0

--- stderr ---
[1/4] ok OB0001L8 /rigtree/alpha/same.txt (11 bytes so far)
[2/4] ok OB0001L8 /rigtree/beta/deep/n.txt (18 bytes so far)
[3/4] ok OB0001L8 /rigtree/beta/same.txt (28 bytes so far)
[4/4] ok OB0001L8 /rigtree/blob.bin (250028 bytes so far)

$ diff -r $HOME/src $HOME/out     # IDENTICAL
$ cat out/alpha/same.txt  -> I am alpha
$ cat out/beta/same.txt   -> I am beta
```

That last pair is the point. The campaign runbook records *"Restore to a
directory uses the basename only… restoring a tree into one directory collapses
same-named files from different subdirectories."* A bulk restore inheriting that
would have written `beta/same.txt` over `alpha/same.txt` and reported success.
`restore tree` maps each catalog path to `<dest>/<path relative to prefix>`.

### 2.3 export refusal — the guard

`OB0001L8` now carries those four files:

```
$ openblade mailslot export OB0001L8
exit=1
stdout bytes = 0

Export refused: Cartridge OB0001L8 still carries archived data; 4 file
instance(s), 250028 bytes on this cartridge; volume group rigtree; e.g.
/rigtree/alpha/same.txt, /rigtree/beta/deep/n.txt, /rigtree/beta/same.txt,
/rigtree/blob.bin. Exporting makes these unrestorable until the cartridge is
imported again. Pass --force if that is what you mean.

$ mtx -f /dev/sg2 status | grep -E "Element 1:|IMPORT"
      Storage Element 1:Full :VolumeTag=OB0001L8
      Storage Element 9 IMPORT/EXPORT:Empty      (…10, 11, 12 also Empty)
```

Nothing moved, stdout stayed empty, exit 1.

### 2.4 sharded archive + tree restore across three tapes

```
$ openblade archive sharded $HOME/src2 --volume-group rigshards \
    --mode block-stripe \
    --lane-barcode OB0002L8 --lane-barcode OB0003L8 --lane-barcode OB0004L8
{"mode": "block_stripe", "blockSizeMb": 128, "filesArchived": 3,
 "bytesArchived": 360000,
 "tapesUsed": ["OB0002L8", "OB0003L8", "OB0004L8"], "errors": []}

$ openblade restore tree $HOME/src2 --dest $HOME/out2
{"filesRestored": 3, "filesFailed": 0, "bytesRestored": 360000,
 "filesVerified": 3,
 "perTapeCounts": {"OB0002L8": 3, "OB0003L8": 3, "OB0004L8": 3},
 "status": "completed"}

$ diff -r $HOME/src2 $HOME/out2    # IDENTICAL
```

Each file was split across all three cartridges and reassembled — that is what
the per-tape counts summing to 9 for 3 files mean. `run_sharded_restore` did the
reassembly; `restore tree` only chose it, because the catalog says the records
are sharded.

Rig left healthy: `scripts/mhvtl/reset.sh` run afterwards, layout back to
`OB0001L8`–`OB0004L8` + `CLN001L8` + `OB0007L8`/`OB0008L8`, all four I/E
elements empty.

---

## 3. Design notes worth knowing

**The refusal lives in the orchestrator, not the CLI.** `TapeOperationOrchestrator`
is this codebase's single choke point for moving media, so `IMPORT`/`EXPORT`
became op types there and the guard sits inside `_export`. Anything that reaches
the orchestrator is guarded, including `POST /tape-ops/execute`. It fails
**closed** on a catalog-less repository: an unknown payload is not an empty one.

**`MOVE` still refuses I/E destinations.** Defect 3.9 (an unvalidated
`dest_slot_id` ejecting a cartridge holding 358 archived files) stays closed —
`_dest_slot` rejects import/export elements exactly as before. The new op types
are the only way to reach the mailslot, and they carry their own guard. There is
a regression test for this.

**The guard is mutation-checked** (both halves), see the test commit message.

**Export writes `cartridges.state = "exported"`.** That flag was read by
`jobs/restore.py`, `jobs/sharded_restore.py` and `jobs/archive.py` and set by
nothing outside a fixture, so media leaving the library used to leave the catalog
claiming the data was online. Import clears it. A unit test demonstrates the
whole consequence chain: export → `run_restore_job` raises `CartridgeOfflineError`.

**A cartridge in the mailslot is still `CartridgeState.IN_SLOT`** at the library
layer — it is inside the machine until a human opens the door. Whether its *data*
is online is the catalog fact above. `find_slot_by_barcode` does not see it, so
nothing can load or unload to it by accident.

---

## 4. Still true after this work (do not assume otherwise)

- **Per-file load/unload remains.** `restore tree` reuses `run_sharded_restore` /
  `run_restore_job` verbatim rather than reimplementing the loop, and those load
  and unload around each file. Grouping the plan by cartridge keeps one tape's
  files consecutive, which helps locality but does **not** remove the swap. The
  campaign runbook's "1,073 restores produced ~1,073 load/unload pairs" still
  describes the cost; fixing it means a batching restore session in the service
  layer, which is a separate piece of work.
- **Sharded archive catalogs files under their absolute source path**, not under
  `/<volume-group>/…` the way `run_archive_job` does. So the prefix for a
  `restore tree` of sharded content is the source directory
  (`restore tree /data/photos`), not `/photos`. This is existing
  `run_sharded_archive` behaviour, identical through the API; the CLI wraps it
  rather than changing it. Worth unifying, deliberately, at some point.
- **`--lanes N` often finds nothing on a fresh volume group.**
  `run_sharded_archive` links a lane cartridge to the volume group only when the
  cartridge row does not already exist, so media the catalog already knows about
  never joins the group. `--lane-barcode` (repeatable) is the reliable form and
  the error message says so. Again: identical on the API path.
- **Restore-to-a-directory basename collapse** is unchanged in the *single-file*
  paths. `restore tree` avoids it; `openblade restore --path … --to <dir>` does
  not.
- **`docs/wiki/reference/cli.md` is stale on this branch** and
  `tests/unit/test_wiki_reference_generated.py` is red because of it. The page is
  a build artifact of `tools/gen_wiki_reference.py`; regenerating it is owned
  elsewhere and these commands will be picked up then.

---

## 5. Simulator support

`MockLibraryBackend` gained `num_import_export_slots` (default **0**, so nothing
that constructs it today changes) with elements numbered after the storage slots,
mirroring the i3. `openblade mock init --ie-slots` defaults to **4** — the rig
and i3 shape — so every mailslot flow above is exercisable with no hardware:

```bash
openblade mock init --slots 8 --drives 2 --cartridges 3 --ie-slots 4
openblade mailslot list
openblade mailslot export MCK00003
openblade mailslot import 9
```

`mock_state.json` round-trips the elements by their own numbers, and a state file
written before I/E slots existed still loads (as a library with no mailslot).
