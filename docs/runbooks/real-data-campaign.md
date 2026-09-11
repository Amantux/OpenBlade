# Real-data campaign — the first pass through the operator surfaces

**Date:** 2026-09-11 · **Rig:** the mhvtl rehearsal library from
[`docs/runbooks/mhvtl-rehearsal.md`](mhvtl-rehearsal.md) — 1 QUANTUM Scalar
changer, 3 IBM LTO-8 drives, 8 storage + 4 I/E slots, real LTFS 2.4.8.4
· **Backend:** `OPENBLADE_BACKEND=real`

The rehearsal proved the *parsers and device plumbing* work. This campaign is
the next question: **does the product do its job when you hand it real data and
drive it the way an operator would** — through the `openblade` CLI and the REST
API, not through pytest.

430 MB, 1,074 source entries, 1,073 files archived, restored and byte-verified
against a seeded manifest. **It found eleven defects** — eight by running it, three more by attacking its own
diff afterwards. They include one that lost data silently, one that could orphan a
third of an archive with a single unconfirmed API call, and a format-token bypass
that this branch's own fix turned from latent into working. None is visible from
the simulator, and none would have been caught by the existing suites, which pass
on all of them.

Everything here is reproducible: `scripts/campaign/` holds the generator, the
manifest, the phase runner and the verifier, so the identical campaign can be
replayed against the real i3.

---

## 1. What was run

### The dataset — `scripts/campaign/gen_dataset.py`

Seeded (`SEED = 20260911`), so the same command on any host produces
byte-identical files and the checked-in manifest still matches.

```bash
python3 scripts/campaign/gen_dataset.py \
    --root /srv/openblade-campaign/data \
    --manifest scripts/campaign/manifest.sha256.json
# generated 1074 entries (1067 files, 5 empty, 1 symlink, 1 dangling) 429.6 MiB
```

| Property | Value |
|---|---|
| Total entries | 1,074 |
| Regular files | 1,067 |
| Empty files | 5 |
| Symlinks | 1 resolvable + 1 dangling |
| Total bytes | 450,491,422 (429.6 MiB) |
| Directory depth | 5 levels, 14 directories |
| Largest file | `datasets/bigblob.dat`, 125,829,120 B |
| Mid-size binaries | 6 × 10–45 MB, 60 × 0.1–4 MB |
| Names | spaces (`reports 2026`), French (`rapport été`), Japanese (`記録`, `日本語データ`) |

Binary content is **incompressible pseudo-random** on purpose: LTO hardware
compression would otherwise make capacity behaviour untestable.

### Environment

```bash
eval "$(scripts/mhvtl/env.sh)"       # OPENBLADE_BACKEND=real, changer + drive nodes
source scripts/campaign/env.sh       # serial map, scratch barcodes, campaign paths
export OPENBLADE_DRIVE_SERIAL_MAP="OBLADE_D01:0,OBLADE_D02:1,OBLADE_D03:2"
```

`scripts/campaign/env.sh` widens `OPENBLADE_SCRATCH_BARCODES` to all six data
cartridges. **On the real i3 narrow that back to genuinely blank media before
running anything** — the campaign formats every barcode it is given.

### Forcing spillover on a virtual rig

The rig's cartridges are 8000 MB and the dataset is 430 MB, so one tape swallows
everything and the roll-to-the-next-tape path never runs. `mhvtl.conf`'s
`CAPACITY` is read by `make_vtl_media` at media *creation* time, so:

```bash
sudo scripts/campaign/set_media_capacity.sh 400    # ~95 MiB usable after LTFS
# ... run the campaign ...
sudo scripts/campaign/set_media_capacity.sh 8000   # back to the rig default
```

**Measured:** a 400 MB mhvtl cartridge presents **99,614,720 bytes (95 MiB)** of
usable LTFS capacity; an 8000 MB one presents **6,569,328,640 bytes (6.1 GiB)**.
Both are far below the nominal figure — LTFS partitioning and the index take the
difference. On a real i3 you do not do any of this; you simply have more data
than a cartridge holds.

---

## 2. Results

### Format — CLI, dry-run + safety-token confirm

```bash
openblade format dry-run --barcode OB0001L8
# {"operation":"format","target":"OB0001L8","affected_barcodes":["OB0001L8"],
#  "warnings":["Destructive operation","Inventory barcode must match confirmation"],
#  "is_destructive":true,"token":"clJuEXd84pxpvvlK89YbMgx3j7bsaoqk702zXO_cQEI"}

openblade format confirm --barcode OB0001L8 --token clJuEXd84pxpvvlK89YbMgx3j7bsaoqk702zXO_cQEI
# {"success":true,"message":"formatted", ... "args":["mkltfs","-d","/dev/sg3","-n","OB0001L8","--force"]}
```

6 cartridges formatted, ~1.2 s each, every one returned to its slot afterwards.
The dry-run token survives across processes (it is persisted in `safety_tokens`),
so the two-phase flow works as an operator would use it — two separate commands.

**This did not work before this campaign.** See defect 2.

### Archive — CLI, with real spillover across 5 tapes

```bash
openblade archive --volume-group campaign-plain --path /srv/openblade-campaign/data
# {"job_id":"8891b52a-770b-4c5e-9964-a83fd5c4ea7f","status":"completed"}   84.0 s
```

| Tape | Files | Bytes | Cartridge used / capacity |
|---|---:|---:|---|
| OB0001L8 | 1 | 125,829,120 | 99,614,720 / 99,614,720 (full) |
| OB0002L8 | 817 | 117,153,907 | 99,614,720 / 99,614,720 (full) |
| OB0003L8 | 251 | 103,700,896 | 96,468,992 / 99,614,720 |
| OB0004L8 | 3 | 71,303,168 | 70,254,592 / 99,614,720 |
| OB0007L8 | 1 | 32,505,856 | 33,554,432 / 99,614,720 |
| **total** | **1,073** | **450,492,947** | 5 tapes |

1,073 records against 1,074 manifest entries: the dangling symlink was skipped
(defect 8) and the resolvable one was dereferenced into a 1,525-byte regular
file, which accounts for the byte total exceeding the manifest's by exactly 1,525.

Tape selection is per file, so a tape is revisited whenever a later file still
fits — five tapes but more than five roll-overs. Cheap here (mhvtl moves media
in ~0.15 s); on a real i3 each roll is a physical load/unload.

### Restore — full, selective, and cross-tape

```bash
python3 scripts/campaign/restore_and_verify.py \
    --volume-group campaign-plain --dest /srv/openblade-campaign/restore/full \
    --manifest scripts/campaign/manifest.sha256.json --all
```

```json
{"requested": 1073, "verified_ok": 1072,
 "symlinks_dereferenced": [{"path": "documents/latest-report.txt",
                            "link_target": "reports 2026/note-0001.txt",
                            "restored_as": "regular file", "restored_size": 1525}],
 "checksum_mismatch": [], "restore_failed": [], "not_in_manifest": [],
 "bytes_restored": 450492947,
 "tapes_touched": ["OB0001L8","OB0002L8","OB0003L8","OB0004L8","OB0007L8"],
 "elapsed_seconds": 612.4}
```

**1,072 of 1,072 regular files byte-identical** (sha256 and size) to the seeded
manifest, across five tapes. Unicode and space-bearing names round-trip exactly:

```
name bytes identical: True     # 記録 0115.txt, source vs restored
NFC form: True
```

Selective restores (`--sample`, `--paths`) verified 11/11 and 4/4 across all five
tapes, including `bigblob.dat` (120 MB), an empty file, and the dereferenced
symlink.

**Cross-tape reassembly** — BLOCK_STRIPE of the 120 MB file across three tapes
with 16 MB blocks, then a parallel three-drive restore:

```bash
curl -X POST :8099/archive/sharded -d '{"source_path":".../bigblob.dat",
  "volume_group":"campaign-block","lane_barcodes":["OB0007L8","OB0008L8","OB0001L8"],
  "mode":"block_stripe","block_size_mb":16}'      # completed, 4.9 s

curl -X POST :8099/restore/ -d '{"catalog_path":".../bigblob.dat",
  "dest_path":".../bigblob-reassembled.dat"}'     # 3.9 s
# size   125829120 expected 125829120
# sha256 2253a2d24d37bbb4 expected 2253a2d24d37bbb4   MATCH
```

Shards landed one per tape (`/block_stripe/<uuid>/bigblob.dat.shard000{0,1,2}`),
`shard_count=3`, `block_size=16777216`, and reassembly was byte-exact.

### Sharded archive — 1,073 files, 3 lanes, all 3 drives

```bash
curl -X POST :8099/archive/sharded -d '{"source_path":"/srv/openblade-campaign/data",
  "volume_group":"campaign-shard","lane_barcodes":["OB0002L8","OB0003L8","OB0004L8"],
  "mode":"stripe"}'
# completed, ~14 min on 6.1 GiB cartridges
```

| Lane | Drive | Files | Bytes |
|---|---|---:|---:|
| OB0002L8 | 0 | 358 | 244,412,043 |
| OB0003L8 | 1 | 358 | 109,016,911 |
| OB0004L8 | 2 | 357 | 97,063,993 |

Lane→drive binding held for the whole run — `tape_op_log` shows drive 0 ↔
OB0002L8, drive 1 ↔ OB0003L8, drive 2 ↔ OB0004L8, 381/362/359 loads. **All three
drives were genuinely used in parallel.**

Selective restore of 12 files spread across the three lanes: **12/12
byte-verified.**

**File *count* is balanced; bytes are not** — 244 MB / 109 MB / 97 MB, a 2.5×
spread, because `barcode = lane_barcodes[index % lane_count]` is round-robin by
file *index* and ignores size. One 120 MB file put lane 0 on the critical path.
[`docs/sharding.md`](../sharding.md) advertises 3× throughput for three lanes;
on this dataset the ceiling is ~1.8×. Not a bug, but the table overstates it.

### Totals across the whole campaign

| | |
|---|---|
| Tape operations | 1,102 loads · 1,102 unloads · 7 formats · 2 moves — all completed |
| Jobs | 5 archive completed · 16 restore completed · 1 restore failed (deliberate, defect 6) |
| Catalog | 1,075 top-level file records |
| Bytes written to tape | ~900 MB (dataset archived twice: plain + sharded) |
| Bytes restored and verified | 450,492,947 full + 181 MB selective |

---

## 3. Defects found and fixed

All eight ship with regression tests, and **every test was mutation-checked** —
the fix reverted, the named test confirmed failing, then restored.

### 3.1 The CLI ignored `OPENBLADE_BACKEND` entirely — it always simulated

`openblade/cli/main.py::_default_config()` built `OpenBladeConfig()` directly
instead of calling `load_config()`. With `OPENBLADE_BACKEND=real` and
`OPENBLADE_REAL_HARDWARE_ENABLED=true` exported and a live library attached:

```
$ openblade inventory
│ 1    │ True     │ VOL001L9 │       ← a simulated 50-slot library
│ 2    │ True     │ VOL002L9 │
...
```

The real library holds 8 slots of `OB000nL8`. The output is plausible, wrong,
and silent. **Every CLI command was affected**, including `format confirm`.

Its hardcoded db/cache/staging/restore paths were already identical to
`load_config()`'s defaults, so honouring the environment changed nothing for a
mock-backed operator. `mock init` now pins `BackendMode.MOCK` explicitly, and the
`mock_state.json` snapshot is skipped for a real context in both directions —
writing it would `AttributeError` on a backend with no `_drives`, and *reading*
it would replace a live library with a simulation, which is worse.

Also: a `mock_state.json` written by an older release used to traceback on every
single command with no documented recovery. It is now moved aside with an
explanatory message and re-seeded.

`tests/unit/test_cli_backend_selection.py` · commit `a13772e`

### 3.2 `format confirm` could never succeed against real hardware

`TapeOperationOrchestrator._format` called `ltfs.format(barcode, ...)` without
loading the cartridge. `mkltfs` runs against a drive node, so **every format of a
cartridge sitting in its slot — the normal state — failed**:

```
TapeOperationFailedError: Tape format operation failed
```

`MockLTFSBackend.format` only needs a barcode, which is exactly why this survived
every simulator test. `_format` now uses the same `_ensure_loaded` +
only-put-back-what-we-took-out discipline as `_write`, including on the failure
path, so a failed format cannot strand a cartridge in a drive. The confirmation
gate is untouched.

### 3.3 …and the operator was told nothing about why

The orchestrator sanitises exceptions into a constant string per op type. That
is correct — it crosses a trust boundary. But it also *logged* only the sanitised
string, so the real cause ("Barcode OB0001L8 is not loaded in a drive") existed
nowhere on the host. The returned/persisted `error` is unchanged; the server log
now carries `cause_type`, `cause` and a traceback.

Same class, same fix, in `nas/hydration.py::_read_archived_bytes`: it caught
every exception and fell back to placeholder bytes, so the checksum guard
reported "tape read failed or data corrupt" — pointing the operator at their
media — when the real cause is that `HydrationExecutor` never loads the
cartridge at all.

`tests/unit/test_tape_orchestrator.py`, `tests/unit/test_nas_hydration.py` ·
commits `c23b0f1`, `bffd191`

### 3.4 structlog wrote to stdout, corrupting the CLI's own JSON

`structlog.configure()` with no arguments uses `PrintLoggerFactory`, which writes
to `sys.stdout` (verified against the installed
`structlog._output.PrintLogger.__init__`: `file or stdout`). So:

```
$ openblade format confirm --barcode OB0002L8 --token ... | jq
json.decoder.JSONDecodeError: Extra data: line 1 column 5 (char 4)
```

Two `tape operation ...` log lines preceded the JSON. Logs now go to stderr.
Commit `c23b0f1`.

### 3.5 Tape capacity was wrong, and usage was reset to zero on every unmount

`RealLTFSBackend._refresh_tape_usage` walked the mount directory, and `unmount()`
called it *after* the umount succeeded. By then the mount point is an ordinary
empty directory, so the walk returned 0 — and 0 is what `jobs/archive.py:240`
then wrote back to the cartridge row. Measured on the rig before the fix:

```
after write  : cap=12000000000 used=5242880  remaining=11994757120
after unmount: cap=12000000000 used=0        remaining=12000000000
```

**OpenBlade believed every tape was empty after every archive job.** `_choose_tape`
— whose entire purpose is to roll to the next tape when this one is full — could
never fire. The hardcoded 12,000,000,000 made it worse: the medium measures
6,569,328,640, so free space was over-reported by 83% even on a blank tape.

Capacity and usage now come from `statvfs` on the live mount, taken *before* the
umount. One syscall instead of an O(files) stat storm after every write, and it
reports the medium's real geometry:

```
after write  : cap=6569328640  used=22020096 remaining=6547308544
after unmount: cap=6569328640  used=22020096 remaining=6547308544
```

`tests/unit/test_hardware_safety.py::TestRealTapeCapacityAccounting` · commit `24271e9`

### 3.6 A full tape was still considered a valid home for a zero-byte file

With capacity finally honest, the archive got further and then died:

```
OSError: [Errno 28] No space left on device:
  '/srv/openblade-campaign/ltfs/OB0001L8/campaign-plain/logs'
```

after 617 of 1,073 files. `_choose_tape` asked
`remaining_capacity(barcode) >= size_bytes`, which is **True for `0 >= 0`** — so
once the first tape was full, every *empty* file in the tree was routed straight
back onto it. An empty file still needs a directory entry and index space, so
LTFS answered ENOSPC; and because that surfaced as an `OSError` rather than a
capacity decision, the job aborted instead of rolling on.

This needs real data to reach: empty files *and* a tape that actually fills.

`_has_room_for()` now treats a tape with zero remaining capacity as unusable for
anything. `tests/integration/test_jobs.py` · commit `2892e67`

### 3.7 STRIPE silently overwrote same-named files — **data loss**

The sharded archive wrote every file to `/stripe/{basename}` — one flat
namespace. Two files with the same name in different source directories that
landed on the same lane wrote to the same on-tape location; the second
overwrote the first, and **both were marked `archived`**. Demonstrated on the rig:

```
/…/collide/alpha/same.txt   sha 0020e5d47ac5   OB0001L8:/stripe/same.txt
/…/collide/beta/same.txt    sha 502c24866d7f   OB0001L8:/stripe/same.txt
```

```
$ curl -X POST :8099/restore/ -d '{"catalog_path":".../alpha/same.txt", ...}'
{"error":"ChecksumMismatchError","detail":"Checksum mismatch:
  expected 0020e5d47ac5…, got 502c24866d7f…"}
```

`alpha/same.txt` was reported archived and is gone. Only the restore-time
checksum caught it — long after an operator would have considered the source
safe to delete. The campaign dataset happened *not* to trigger this (its names
are globally unique), so it was reproduced deliberately; that is worth noting,
because a dataset with per-directory naming (`2026-01/report.pdf`,
`2026-02/report.pdf`) hits it immediately.

`_stripe_tape_path()` now mirrors the source tree under `/stripe`, the way the
non-sharded path already mirrors it under the volume group. Re-verified on the
rig: `/stripe/alpha/same.txt` and `/stripe/beta/same.txt`, both restoring to
their own bytes. Existing media are unaffected — restore reads
`file_instances.tape_path` as stored rather than recomputing it.

Self-review of that fix then found two defects *in the fix*: `Path.relative_to`
does not normalise, so a `source_path` of `/data/..` (operator-supplied, straight
from the API body) produced `/stripe/../etc/passwd` and escaped the mount; and
`relative_to(itself)` returns `Path(".")` rather than raising, collapsing a
single-file source to a bare `/stripe`. Both are closed — normalised lexically
with `os.path.normpath` (never `resolve()`, which touches the filesystem and
follows symlinks) plus a component filter. Worth recording because the
single-file case had a test that *passed* on the broken version: MockLTFSBackend
accepts any path string, so only probing the function directly exposed it.

`tests/integration/test_sharded_archive_restore.py` · commits `7c041a5`, `441c11e`

### 3.8 A failed sharded archive recorded no reason anywhere

The first full sharded run wrote **3 of 1,073 files in ten minutes** and surfaced
as `failed_recoverable` with `error: null` and not one log line in 4,299 lines of
server output. `errors` was collected per batch, returned in
`ShardedArchiveResult`, and discarded by the API route (which answers
`{job_id, status:"pending"}` regardless); `update_job_state` was called without
an error argument, and the per-batch `except` had no logging at all.

The handler now logs with a traceback, and a bounded, de-duplicated summary is
persisted on the job. Commit `7c041a5`.

### 3.9 An unvalidated integer ejected a cartridge to the mailslot

`_dest_slot` `int()`d an operator-supplied value and passed it straight to
`mtx transfer`. mtx element numbers do not stop at the storage slots — they
continue into the import/export magazine.

```
$ curl -X POST :8099/tape-ops/execute \
    -d '{"op_type":"move","barcode":"OB0003L8","slot_id":3,"extras":{"dest_slot_id":9}}'
200 OK

$ mtx -f /dev/sg2 status
      Storage Element 9 IMPORT/EXPORT:Full :VolumeTag=OB0003L8
```

No confirmation, no safety token, no warning. That cartridge held **358 archived
files**, and `inventory()` reports data storage slots only — deliberately, see
`hardware/mtx.py` — so afterwards:

```
$ openblade inventory | grep -c OB0003L8
0
```

The tape was invisible to the product. Every file on it became unrestorable
("Barcode not found in inventory"). **One unconfirmed integer orphaned a third of
an archive.**

Moving media out of the library is an export, and the product's position on
export is already explicit — `routes_aml_move_medium` rejects moveClass
import/export on i3/i6 with 422. The orchestrator now agrees: a destination
outside the library's own storage slots is rejected during validation, naming the
valid range. Verified live:

```
HTTP 400 {"detail":"destination slot 9 is not a data storage slot in this library
  (valid: 1-8); moving media to an import/export element is not supported"}
```

and the cartridge stayed in slot 3. `tests/unit/test_tape_orchestrator.py` ·
commit `9d82533`

### 3.10 The format safety token was self-issued — found by adversarial review

Not found by running the campaign; found by attacking the campaign's own diff
afterwards, which is why the reviewer step is not optional.

`extras` on `POST /tape-ops/execute` is bound straight from the request body, and
`_validate_request` accepted `extras["confirmed_format"] is True` — a bare boolean
an operator can type — as the entire gate. `_format` then **minted its own
`SafetyToken`** to satisfy the confirmation it was supposed to be checking:

```json
{"op_type":"format","barcode":"OB0001L8","extras":{"confirmed_format":true}}
→ status: COMPLETED   result: {"formatted": true}
```

AGENTS.md: *"Never perform format or erase operations without positive barcode
confirmation and a cryptographically valid safety token."*

The hole predates this branch, **but defect 3.2 is what made it lethal.** Before
that fix, on real hardware the same request died at "Barcode … is not loaded in a
drive" for any cartridge in a slot — the normal state. Fixing the load made the
bypass reach `mkltfs`. A fix that converts a latent auth hole into a working one
has to close the hole in the same change, so:

- `_format` no longer mints anything. No `FormatConfirmation` in extras →
  `OperationNotConfirmedError`. It also calls `confirmation.validate(barcode)`, so
  a token issued for one cartridge cannot format another.
- `POST /ltfs/format` now goes through `FormatService.confirm`, which checks the
  persisted one-time token from the dry-run. **This is a deliberate contract
  tightening**: the route previously ignored the `safetyToken` its own callers
  were already sending (`tests/i3/test_07_ltfs.py:53` sends one), and formatted
  on `confirm: true` alone. It now returns 422 without a token and 403 on a bad
  one. `tests/i3/test_07_ltfs.py::test_format_with_timing` sends a placeholder
  token and will need a real dry-run token; that suite needs a live emulator and
  was not run here.

While in `routes_ltfs.py`: `/ltfs/mount` and `/ltfs/unmount` returned
`str(exc)` from a bare `except Exception` on an unauthenticated route, leaking
argv, device paths and raw LTFS stderr. Curated message out, cause to the log.

`tests/unit/test_tape_orchestrator.py::test_format_refuses_a_bare_confirmed_format_flag`

### 3.11 `statvfs` could read the host disk — found by adversarial review

The capacity fix (3.5) trusts a `mounted` flag that is the **caller's belief**.
Two ways to be wrong, neither hypothetical: `OPENBLADE_HARDWARE_DRY_RUN=true`
with `BackendMode.REAL` is a supported, tested config in which `mount()` creates
the mount point and nothing is ever mounted; and the documented unmount retry
leaves the handle active after the filesystem is gone. In both, `statvfs` reads
the host filesystem — and `jobs/archive.py:255` copies that straight onto the
cartridge row. The reviewer reproduced it:

```
capacity_bytes: 1965172678656   used_bytes: 674029903872   ← the host disk
```

A ~2 TB "LTO-8 cartridge" in the catalog means spillover never fires again — the
exact bug 3.5 exists to fix, reintroduced from the other direction. And on a
nearly-full host disk, `remaining == 0` makes the new `_has_room_for` guard
refuse *every* tape.

`_is_distinct_mount()` now compares `st_dev` against the parent's before
believing the numbers, and **fails closed**: if either stat fails, the answer is
no. Verified against real LTFS — `True` while mounted, `False` after unmount.

### 3.12 Other review findings, fixed

- **`openblade mock inventory` / `mock load` / `mock unload` drove the real
  library.** Defect 3.1 switched only `mock init` to a pinned mock config; the
  other three went through the env-driven one, so in the campaign's own shell
  `openblade mock load --slot 3 --drive 0` issued a real `mtx load`. All four now
  use `_get_mock_context()`.
- **Two of the 3.2 regression tests were vacuous against a full revert.** With
  `_format` fully reverted, only `test_format_loads_the_cartridge_into_a_drive_first`
  failed; the two unload tests passed *trivially*, because with no load the
  cartridge was never in a drive. They now also assert `drive_at_format` and that
  the cartridge returns to its **original** slot, and all three fail on a full
  revert. The runbook's "every test was mutation-checked" claim was true of the
  reverts I ran and false of the one I did not.
- **`test_default_config_keeps_home_relative_paths` failed inside the campaign's
  own documented shell** — the fixture cleared only the two backend variables,
  while `scripts/campaign/env.sh` exports `OPENBLADE_DB_URL` and friends. Fixture
  widened.
- **A flaky capacity assertion.** `used_bytes` was asserted as an equality
  against a live host filesystem, so a parallel test writing a temp file moved
  `f_bavail` between the two reads. It is now a range, and the arithmetic tests
  stub the mount check rather than depending on it.
- **One tracked `.pyc` was committed** in `bffd191` and has been restored. 66 are
  tracked on `master` despite `.gitignore` covering them, and they are
  `cpython-310` artefacts on a repo pinned to 3.12 — worth a `git rm -r --cached`
  pass that is out of scope here.

### Review findings NOT fixed

Reported rather than changed, because each is pre-existing, sits in a risky area,
and the campaign had already made enough changes there:

- **`jobs.error` now carries raw exception text at an unauthenticated boundary.**
  `GET /jobs/` and `/jobs/{id}` have no auth dependency, and 3.8's error summary
  routes `str(exc)` — which for a `CommandError` is argv plus raw `mkltfs` stderr
  — into that field. `archive.py:333` already did this; 3.8 widened it. The right
  fix is a sanitiser shared with `_safe_error_message`, and the existing test only
  asserts the field is non-empty, so it locks in the leak. **This one should be
  fixed before merge.**
- **`_has_room_for` only rejects a tape with *exactly* zero bytes free.** The
  stated cause is LTFS index overhead, so the threshold should be a reserve, not
  1 byte; a tape with 512 bytes left still ENOSPCs on an empty file.
- **`RealLTFSBackend._tapes` is never hydrated from `cartridge.capacity_bytes`.**
  After a restart every tape reports the fictional 12 GB default again, so spill
  selection is wrong until each tape has been mounted once. The catalog already
  holds the measured value.
- **`_format` takes the wrong drive lock** when `request.drive_id` disagrees with
  where the cartridge actually is, and `RealLibraryBackend.unload` has **no**
  `can_unload_drive` guard at all — the "never unload while LTFS is mounted or
  dirty" non-negotiable is enforced only in the simulator. That is a real gap in
  the product, not in this branch.
- **A failed unload in `_format`'s `finally` masks a successful format**: the
  media is wiped, the exception replaces the result, and `FormatService.confirm`
  never marks the cartridge formatted.
- **`run_campaign.sh` formats six cartridges with no prompt**, and keys on its own
  `CAMPAIGN_TAPES` rather than the `OPENBLADE_SCRATCH_BARCODES` the runbook tells
  you to narrow (that variable is read only by `tests/hardware/conftest.py`).
  `scripts/mhvtl/format-scratch.sh` has a confirmation step; this does not. **On
  the real i3, set `CAMPAIGN_TAPES` explicitly and re-read it before running the
  format phase.**
- **`_dest_slot`'s error message** renders `min-max` of a slot set that is not
  guaranteed contiguous (`SAMPLE_MTX_HIGH_ADDRESSES` has elements at 4096-4099).
  The guard itself is a set-membership test and is correct; only the message
  could mislead.

---

## 4. Function matrix

Read this as the honest state of the operator surfaces, not a wish list.

### Works

| Function | Surface | Evidence |
|---|---|---|
| Library inventory | CLI `openblade inventory`, `GET /inventory/` | 8 slots + 3 drives, live, correct |
| Format: dry-run → token → confirm | CLI `openblade format dry-run` / `format confirm` | 6 cartridges, real `mkltfs`; token survives across processes |
| Volume group create | CLI `openblade volume-group`, `POST /volume-groups/` | 4 groups |
| Archive with spillover | CLI `openblade archive` | 1,073 files / 450 MB over 5 tapes |
| Sharded archive, STRIPE | `POST /archive/sharded` **(API only)** | 1,073 files over 3 lanes and 3 drives in parallel |
| Sharded archive, BLOCK_STRIPE | `POST /archive/sharded` **(API only)** | 120 MB over 3 tapes, 16 MB blocks |
| Restore, single file | CLI `openblade restore`, `POST /restore/` | 1,073 restores, checksum-verified |
| Restore, cross-tape reassembly | `POST /restore/` (auto-detects shards) | BLOCK_STRIPE reassembly byte-exact |
| Restore checksum enforcement | both | Caught the STRIPE collision; quarantines on mismatch |
| Catalog listing | CLI `openblade catalog`, `GET /catalog/` | 1,075 records, paged |
| Job list / status / failure | CLI `openblade jobs`, `GET /jobs/`, `GET /jobs/{id}` | 22 jobs; failures now carry a reason (3.8) |
| Load / unload / move | `POST /tape-ops/execute` **(API only, RBAC)** | real `mtx` load, unload, slot-to-slot transfer |
| Health / readiness | `GET /health`, `/healthz`, `/readyz`, `/version` | DB latency, component detail |
| Library + drive status | `GET /status/library` (auth) | per-drive state and mount state |
| Safety self-check | `GET /safety/check` (auth) | orchestrator routing, hardware-guard scan |
| Hardware validation | CLI `openblade hardware connect-i3`, `validate-ltfs` | from the rehearsal; unchanged |
| Unicode / spaces / empty files | end to end | byte-identical round trip, NFC preserved |

### Broken or misleading

| Function | Surface | What is wrong |
|---|---|---|
| `GET /ltfs/status` | API | Returns `{"status":"unknown"}` on the real backend — it swallows every error, and `RealLTFSBackend` has no `to_json()`. Useless where it matters. |
| `GET /status/catalog` | API | Reported `total_file_records: 0` while the catalog held 1,075 — it counts NAS `nas_file_records`, a different table. |
| `GET /dashboard/stats` | API | `totalAssignedTapes: 0`, `totalTapeCapacityBytes: 0` after archiving 450 MB (see below). |
| `GET /volume-groups/` | API | Every group reports `barcodes: []`. |
| `GET /cartridges/` | API | Every cartridge reports `formatted: false` after being formatted through the product — the format path never writes the flag back. |
| Cartridge write-back, sharded path | `jobs/sharded_archive.py` | After a full sharded archive, **no cartridge row has a `volume_group_id`, `used_bytes` or `capacity_bytes`**. The non-sharded path does this at `archive.py:240`; the sharded path does not. This is what empties the four surfaces above, and it means a second sharded archive onto the same lanes treats them as blank. |
| Spillover, sharded path | `jobs/sharded_archive.py` | No capacity check and no roll-to-next-tape at all. On 95 MiB cartridges the 430 MB dataset failed every batch. A lane filling up is a raw `ENOSPC` in `errors[]`, not a tape change. |
| Catalog namespace, sharded path | `jobs/sharded_archive.py` | Records are keyed on the **absolute source path** (`/srv/…/data/x.bin`), not `/{volume_group}/{relative}` as the non-sharded path does. The volume group is not part of the key, so archiving the same source twice in different modes **merges into one record with mixed instances** — observed when `bigblob.dat` was archived STRIPE then BLOCK_STRIPE. |
| `/nas/fuse/open` | API | Returns `{"action":"queue_hydration","message":"File is offline. Hydration queued."}` — nothing is queued. No restore job, no executor, no queue append. The message is untrue. |
| `POST /nas/datasets/{id}/export` | API | Flips two database columns to EXPORTED. No robot, no tape op, no audit record. The tape does not move; the catalog then claims the data left. |
| `TapeOpType.EJECT` | none | Never constructed anywhere, and non-functional if constructed: no library backend defines `eject`, so it silently falls through to an unload into a storage slot. |
| `GET /catalog/seed-demo` | API | Shadowed by `GET /catalog/{file_id}`, declared earlier — always 404s as `file_id="seed-demo"`. |
| `Dockerfile` `OPENBLADE_BACKEND=simulator` | container | Not a valid `BackendMode`; `load_config()` catches the `ValueError` and silently falls back to `mock`. |

### Does not exist

| Function | Status |
|---|---|
| **FUSE / NAS mount** | **No FUSE filesystem exists.** No `fusepy`/`pyfuse3`/`llfuse` in `pyproject.toml`; grepping the tree for them returns nothing. `CatalogFilesystem` is a plain 98-line in-process object whose only production consumer is `openblade catalog`. There is no mount command, entrypoint, or unit file. **This is not an environment limitation** — this host has `/dev/fuse` and `fusermount3 3.10.5` available; the feature is simply unimplemented. `docs/fuse.md` says so ("a lightweight namespace abstraction… so future kernel FUSE work can reuse the same contracts"). |
| **Import / export (I/E slot) flows** | No CLI command, no native REST route, nothing in `openblade/hardware/`. `MtxStatus.import_export_slots` is parsed and has **zero consumers**. The AML surface returns 422 for import/export moveClass on i3. The only way to reach an I/E element was the unvalidated `dest_slot_id` of defect 3.9, now closed. |
| **Bulk / directory restore** | `openblade restore` and `POST /restore/` take one catalog path. There is no "restore this volume group" anywhere. The NAS hydration path does bulk work but is pool/dataset-scoped and never populated by the archive jobs. `scripts/campaign/restore_and_verify.py` loops because it has to. |
| **CLI load / unload / move on a real library** | `openblade mock load` / `mock unload` are simulator-only (and now explicitly pinned to `BackendMode.MOCK`). Real media movement is API-only, through `POST /tape-ops/execute`. |
| **CLI sharded archive** | API only. |
| **Drive health / TapeAlert** | No native route. Drive state comes from `/inventory/` and `/status/library`. The rehearsal already recorded that mhvtl does not emulate TapeAlert, so this stays unexercised until the i3. |
| **`WRITE` / `READ` / `VERIFY` tape ops** | No dedicated CLI command or route; reachable only by hand-crafting a `/tape-ops/execute` body. |
| **Virtual namespace over archived data** | `GET /virtual/ls?path=/` returns `{"entries":[],"total_entries":0}` after archiving 450 MB. The NAS namespace is dataset-backed and the archive jobs never create datasets — the two halves of the product are not connected. |

---

## 5. Behaviours worth knowing (not defects)

- **Symlinks are dereferenced.** A resolvable symlink is archived as a regular
  file holding the target's bytes; the link is not preserved. Restoring it
  produces a file, not a link.
- **A dangling symlink is silently skipped.** No warning, no error, no entry in
  the job result — the job reports `completed` with a file count that is quietly
  lower than the source tree. In this campaign: 1,073 archived from 1,074
  entries.
- **Restore to a directory uses the basename only.** `openblade restore --to
  /some/dir` writes `/some/dir/{basename}`, so restoring a tree into one
  directory collapses same-named files from different subdirectories. Pass an
  explicit per-file destination (what the campaign verifier does).
- **`RealLTFSBackend.write_file` reads the whole file into memory**
  (`source.read_bytes()`). The 120 MB blob was fully buffered. For a product
  targeting multi-TB media this is a scaling limit worth a look.
- **A tape this process has never mounted still assumes 12 GB.** There is nothing
  to measure until it is mounted, so `_choose_tape` can pick an over-estimated
  tape on the first file after a restart. Left as-is rather than papered over
  with a config knob; recorded here instead.
- **Single-file restore load/unloads per file.** 1,073 restores produced ~1,073
  load/unload pairs. 612 s on mhvtl; on a real i3, where a tape change is
  measured in tens of seconds, a full restore of this shape is impractical. This
  is the strongest argument for a bulk restore path.
- **STRIPE batching is chatty.** One batch of *lane_count* files does
  load + mount + write + unmount + unload on every lane. 1,073 files over 3 lanes
  produced 358 batches and ~2,166 robot moves.

---

## 6. Gate results

| Gate | Result |
|---|---|
| `ruff check .` | **13 findings — all pre-existing on `master` (14 there).** Identical rule-for-rule except one `F841` this branch fixes. Zero new findings; `ruff check` is clean on every file this branch touches. |
| `ruff format --check` | Already failing on `master` for the touched files; unchanged. |
| `pytest tests/unit tests/integration tests/safety` | 1,315 passed, **2 failed — both reproduced on a clean `master` checkout** (`test_controller_isolation.py::test_moveMedium_accepted_with_service_token`, `test_nas_dataset_api.py::test_post_verify_returns_200_with_checksums`). Not introduced here. |
| New tests | 31 across 6 files, **all mutation-checked, including against full reverts** |
| `tests/hardware/ -m real_hardware` | Not re-run; the rig was in campaign use. Worth a pass before merge. |

`make lint` is red on `master` and stays red — `make all` cannot currently pass
on this repo for reasons that predate this work. Flagging rather than fixing:
it is outside the campaign's scope and one of the 11 findings
(`assert ... or True` in `tests/i3/test_10_fault_scenarios.py:61`) is a genuinely
vacuous assertion that deserves its own look.

---

## 7. Reproducing this

```bash
# 0. rig up, per docs/runbooks/mhvtl-rehearsal.md
sudo scripts/mhvtl/setup.sh
eval "$(scripts/mhvtl/env.sh)"
source scripts/campaign/env.sh

# 1. dataset (deterministic; the checked-in manifest still matches)
python3 scripts/campaign/gen_dataset.py \
    --root "$CAMPAIGN_DATA" --manifest scripts/campaign/manifest.sha256.json

# 2. small media, so 430 MB actually overflows a cartridge  [RIG ONLY]
sudo scripts/campaign/set_media_capacity.sh 400

# 3. CLI phases — every command and exit status lands in $CAMPAIGN_LOG/campaign.log
scripts/campaign/run_campaign.sh inventory
scripts/campaign/run_campaign.sh format
scripts/campaign/run_campaign.sh vg
scripts/campaign/run_campaign.sh archive

# 4. restore + byte-verify against the manifest
python3 scripts/campaign/restore_and_verify.py \
    --volume-group campaign-plain --dest "$CAMPAIGN_RESTORE/full" \
    --manifest scripts/campaign/manifest.sha256.json --all \
    --report "$CAMPAIGN_LOG/restore-full.json"

# 5. the API-only surfaces
python3 -m uvicorn openblade.api.main:app --host 127.0.0.1 --port 8099 &
scripts/campaign/run_campaign.sh sharded
scripts/campaign/run_campaign.sh status

# 6. put the rig back
sudo scripts/campaign/set_media_capacity.sh 8000
sudo scripts/mhvtl/reset.sh
```

**On the real i3**: skip steps 2 and 6, narrow `OPENBLADE_SCRATCH_BARCODES` to
genuinely blank cartridges, and expect step 4 to take hours rather than ten
minutes — a real tape change is not 0.15 s.

---

## 8. What this campaign did not cover

- **`tests/i3/`.** Not run — it needs a live emulator fleet, and defect 3.10
  deliberately tightens `POST /ltfs/format` in a way that
  `test_07_ltfs.py::test_format_with_timing` will notice. Run it before merge.
- **Concurrency.** One operator, one job at a time. `_ARCHIVE_REQUEST_LOCK`
  serialises archive requests but `POST /restore/` has no lock at all, and
  `DriveScheduler` is constructed per request rather than shared — so two
  concurrent sharded jobs can each believe they own all three drives. Untested,
  and it looks like a real hazard.
- **Failure injection against real hardware** — a drive going offline mid-write,
  a mount failing mid-batch, power loss between write and commit.
- **Resuming a `failed_recoverable` sharded job.** The staged-PENDING design says
  it is resumable; nothing resumes it and there is no surface that tries.
- **The AML/iBlade emulator surfaces.** Out of scope here; they have their own
  parity gates.
- **Drive health, TapeAlert, cleaning cycles, WORM, media generations** — mhvtl
  does not emulate them. Still waiting on the i3, as the rehearsal said.
- **Multi-copy / replication**, and any `copies_required > 1` policy path.
