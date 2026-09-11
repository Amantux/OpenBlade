# Drives & changer operations

This is the page to read before the first multi-drive write to real hardware.
It covers load/unload/move, the guards around them, and the drive-correlation
problem — the one that quietly writes your data to the wrong cartridge.

---

## The wrote-to-wrong-drive problem

Two independent numbering systems meet inside OpenBlade, and **nothing
guarantees they agree**.

- **The robotics side** speaks *Data Transfer Element* numbers. `mtx -f <changer>
  load 3 1` puts the cartridge from slot 3 into DTE 1. The cartridge physically
  lands in that element.
- **The data side** speaks host device nodes. `/dev/nst0`, `/dev/nst1`, … or the
  SCSI generic nodes `/dev/sg1`, `/dev/sg4`, …

When OpenBlade writes, it asks `find_drive_by_barcode()` for an **element index**
and then asks `drive_device()` for the **device node** to hand to LTFS. If the
mapping between those two is wrong, the changer puts your cartridge in element 1
while LTFS opens whatever device happens to sit at index 1 of the device list —
*a different physical drive, holding a different cartridge*. That cartridge then
gets formatted or written.

Without configuration, the mapping is **positional**: the order of
`OPENBLADE_DRIVE_DEVICES`, or, if that is unset, SCSI-address order from
discovery. That ordering is an assumption. From the mhvtl rehearsal, across
reboots of one unchanged rig:

| boot | target 1 | target 2 | target 3 |
|---|---|---|---|
| A | `/dev/st0` | `/dev/st1` | `/dev/st2` |
| B | `/dev/st2` | `/dev/st1` | `/dev/st0` |
| C | `/dev/st1` | `/dev/st2` | `/dev/st0` |

Device names are not stable across reboots. Serial numbers are.

With one drive this cannot bite you. With two or more, it can.

---

## `OPENBLADE_DRIVE_SERIAL_MAP`

Declare which drive serial sits in which element:

```bash
OPENBLADE_DRIVE_SERIAL_MAP=10WT073820:0,10WT073821:1,10WT073819:2
```

Format: comma-separated `<serial>:<element_index>`. The index is the **0-based
mtx Data Transfer Element number**. Whitespace around entries is stripped; empty
entries between commas are skipped. The separator is the *last* colon, so a
serial containing colons still parses.

What happens when it is set: at backend construction — *before any load or write
can happen* — OpenBlade runs `sg_inq` against every configured drive, reads the
real unit serial numbers, and either builds the element→device map from your
declaration or **refuses to start**.

Get the serials with:

```bash
lsscsi -g
sg_inq /dev/sg1        # "Unit serial number:"
```

`scripts/mhvtl/setup.sh` prints the correlation table on every run for the
virtual rig.

### Success looks like

```
drive correlation applied from OPENBLADE_DRIVE_SERIAL_MAP (declared serials
match the attached drives; element assignment is operator-declared and cannot
be machine-checked): DTE 0 -> /dev/nst0 (10WT073820), ...
```

### Failure messages, and what each means

| Message (abridged) | Cause |
|---|---|
| `does not match the drives that are attached. Declared but not found: […]. Attached but not declared: […]` | Your serial list and the hardware disagree. Refuses rather than guess. |
| `declares drive element(s) […] but the changer reports only N Data Transfer Element(s) (0..N-1). Note the map uses 0-based element indices, while the i3 web UI numbers drive bays from 1.` | **The classic mistake.** You copied bay numbers 1,2,3 out of the i3 UI. |
| `is set but these devices report no unit serial number … Check \`sg_inq <device>\` and drive permissions` | `sg_inq` ran but got nothing back. |
| `sg_inq is not installed … Install sg3_utils` | Missing dependency. |
| `sg_inq failed on <dev> (rc=N). Check the device path, permissions (the 'tape' group), and that the drive is not held by another process.` | Usually permissions, or LTFS is holding the drive. |
| `Two or more configured devices report the same unit serial number` | The same physical drive is listed twice under two names. |
| `lists serial 'X' more than once` / `lists drive element id N more than once` | Duplicate in your map string. |
| `entry 'X' is not of the form '<serial>:<drive_element_id>'` | Syntax. Parse errors fire at config load, before anything touches hardware. |
| `The changer reports N drive element(s) but only M device(s) are configured … a job scheduled onto the missing element would strand a cartridge in it` | List every drive in `OPENBLADE_DRIVE_DEVICES`. |

Surface it cleanly with:

```bash
openblade hardware connect-i3
```

This is the one CLI command with a curated error handler — a correlation failure
prints the message and exits 1 rather than dumping a traceback.

### ⚠️ What the check does *not* prove

The check proves the **set** of attached drives equals the set you declared. It
never observes which serial is actually in which element. **A transposed or
rotated declaration passes and reports `serials_verified: true`** — and
transposition is exactly what copying 1-based UI bay numbers produces.

The authoritative fix would be READ ELEMENT STATUS with the DVCID bit. It is
deliberately not implemented, because no validated capture from a real i3 exists
yet.

So do the empirical check by hand, once, before trusting it:

1. Load a scratch cartridge into element *k* only.
2. Confirm that **only** the device you correlated to element *k* reports a tape
   online.
3. Repeat per element.

`docs/hardware-setup.md` has this procedure in full.

---

## Device nodes: `st` vs `nst` vs `sg`

Three node families, and picking the wrong one produces misleading errors.

| Node | Use |
|---|---|
| `/dev/stN` | **rewinding**. Closing it rewinds the tape. Avoid. |
| `/dev/nstN` | no-rewind. What you put in `OPENBLADE_DRIVE_DEVICES`. |
| `/dev/sgN` | SCSI generic. What LTFS and `sg_inq` actually need. |

**`stN` and `sgN` numbers are allocated independently and do not correlate.**
This rig routinely produces `st0 -> sg1` and `st2 -> sg4`. OpenBlade reads the
mapping from `/sys/class/scsi_tape/<name>/device/scsi_generic/` rather than
deriving it by name, via `resolve_sg_device()`, applied at format, mount-readonly
and mount-readwrite.

Two failure modes that motivated it, both worth recognising:

- **LTFS handed `/dev/st0` does not fail cleanly.** It reads the wrong device and
  reports `LTFS11253E No index found in the medium` / `medium consistency check
  failed`, which reads as *blank or corrupt cartridge*. Operators conclude the
  tape is bad. It isn't.
- **`sg_inq` on a rewinding node exits 50** (`close error: No medium found`)
  whenever the drive is empty, because closing `stN` attempts a rewind. The
  inquiry itself succeeded; only the close failed.

Drive serial probes deliberately use the `sg` node, because the `st` driver
allows a single open and `sg_inq` on `/dev/nstN` fails EBUSY while LTFS holds the
drive.

### Known defect: `_ordered_drive_devices` returns the rewinding node

The discovery fallback prefers `block_device` over `sg_device`, and `lsscsi`
reports `/dev/st0` in the block column. So when `OPENBLADE_DRIVE_DEVICES` is
unset, **every consumer of `drive_device()` gets a rewinding node.** `ltfs.py`
defends itself with `resolve_sg_device()`, but the source should prefer the `sg`
or at minimum the `nst` node. This is reported, not fixed
(`docs/runbooks/mhvtl-rehearsal.md` §4).

**Set `OPENBLADE_DRIVE_DEVICES` explicitly with `nst` nodes.** Do not rely on
discovery order.

---

## The operations

Eight operation types exist. The generic entry point for all of them is
`POST /tape-ops/execute`; a few have dedicated endpoints.

| Operation | CLI | Dedicated endpoint |
|---|---|---|
| `load` | `openblade mock load --slot N [--drive N]` | `POST /ltfs/mount` (loads first); `POST /aml/media/move` |
| `unload` | `openblade mock unload --drive N --slot N` | `POST /aml/media/move` (drive → slot) |
| `format` | `openblade format dry-run` / `confirm` | `POST /ltfs/format` — ⚠️ [bypasses the token flow](formatting-tapes.md) |
| `write` | — (via `openblade archive`) | — |
| `read` | — (via `openblade restore`) | — |
| `move` | — | `POST /aml/media/move` (slot → slot) |
| `verify` | — | — |
| `eject` | — | — |

Note the `mock` prefix on load/unload: those CLI commands are under the
simulator group and operate on the mock library state file.

Semantics worth knowing:

- **Load** defaults the slot to wherever the barcode currently is, and defaults
  the **drive to 0** if you do not say. Error if the barcode is not in a slot.
- **Unload** defaults the drive to wherever the barcode is, and the target slot
  to the *first empty slot*. `No empty slot is available for unload/eject` if
  there is none.
- **Move** requires distinct source and destination slots.
- **Eject** uses the backend's `eject()` if there is one, otherwise falls back to
  an unload.
- A failed operation is **returned as a `failed` record, not raised** (the one
  exception being an unconfirmed format). Callers must inspect `record.status` —
  the CLI's `mock load`/`unload` pass `raise_on_failed=True`, but a raw
  `/tape-ops/execute` call does not.
- Failure messages are deliberately generic per op type ("Tape load operation
  failed"). Detail is in the `tape_op_log`, not in the response.

Audit trail: `GET /tape-ops/{op_id}` and `GET /tape-ops` list every orchestrated
operation with its result and error.

### Concurrency

Read and verify take a **per-barcode** lock. Write and format take a
**per-drive** lock. Load, unload, move and eject take **no orchestrator lock at
all** — serialisation there depends on the library itself.

The simulator additionally serialises the changer with a non-blocking lock and
raises `ChangerBusyError: Changer is already moving media`. The real backend has
no such check; it trusts `mtx`.

---

## The unload-while-mounted guard

The rule: a drive may be unloaded only when its mount state is `unmounted` and
its drive state is `loaded` or `failed`. `mounted_ro`, `mounted_rw` and `dirty`
all block it, with:

```
TapeMountedError: Drive 0 cannot be unloaded while mounted_rw
```

> ⚠️ **This guard is enforced in the simulator only.** `RealLibraryBackend.unload()`
> calls the changer directly and merely records the new mount state afterwards.
> On real hardware the check does not exist. `docs/safety.md`, `README.md` and
> `docs/architecture.md` all state the guarantee without that qualification.

What *does* protect real tapes is the unmount path. `umount` returns as soon as
the kernel detaches the filesystem, but the LTFS FUSE process lives on briefly to
flush its index and close the drive. OpenBlade polls `/proc` until the process
releases the device (bounded), and judges the unmount on **release, not on
`umount`'s exit status**. Results carry `device_released`. If `/proc` cannot be
read at all, the drive is treated as **still held** — "I could not look" must
never read as "safe to yank the cartridge".

Without that wait, an unmount→remount or unload→reload cycle fails with:

```
LTFS30210I Cannot open device: failed to open /dev/sg2 (16).    # EBUSY
```

The argv matching behind it is explicitly a backstop, not a lock: a wrapper
script or a differently-spelled mount point is missed.

---

## Environment variables

### Backend and safety gate

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_BACKEND` | `mock` | `mock` or `real`. Invalid values silently fall back to `mock`. |
| `OPENBLADE_REAL_HARDWARE_ENABLED` | `false` | Must be exactly `true`. Both this and `BACKEND=real` are required. |
| `OPENBLADE_HARDWARE_DRY_RUN` | `false` | Log every command line, execute nothing. |
| `OPENBLADE_ROBOTICS_TRANSPORT` | `scsi` | `scsi` (mtx) or `webservices` (Scalar HTTP API). The latter requires `OPENBLADE_SCALAR_URL` and cannot supply a `drive_device`. |
| `OPENBLADE_ENV` | `development` | `production` turns on config validation and mandatory service token. |

### Devices

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_CHANGER_DEVICE` | auto-discover | Changer node; prefer the `sg` node. |
| `OPENBLADE_DRIVE_DEVICES` | auto-discover | Comma-separated **no-rewind** nodes **in element order**. Authoritative when set. |
| `OPENBLADE_DRIVE_SERIAL_MAP` | unset (unverified positional) | See above. |
| `OPENBLADE_TAPE_GID` | **required in the hardware compose file** | Numeric host group owning the tape/sg nodes: `stat -c %g /dev/sg0`. |

### Paths

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_DB_URL` | `sqlite:///~/.openblade/openblade.db` | Catalog DB. **Ignored by the CLI** — see [the catalog](the-catalog.md). |
| `OPENBLADE_CACHE_DIR` | `~/.openblade/cache` | Hydration cache. |
| `OPENBLADE_STAGING_DIR` | `~/.openblade/staging` (upload route uses `/tmp/openblade-staging`) | Upload staging. |
| `OPENBLADE_RESTORE_DIR` | `~/.openblade/restore` (hydration uses `/tmp/openblade-restore`) | Restore output. |
| `OPENBLADE_LTFS_MOUNT_ROOT` | `~/.openblade/ltfs` | One mount dir per barcode beneath this. |
| `OPENBLADE_INBOX_ROOT` | `/var/lib/openblade` | SFTP gateway inbox. |

Note the two pairs of divergent defaults — the config object and the routes read
the same concept with different fallbacks. Set them explicitly.

### API, auth, logging

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_ADMIN_PASSWORD` | insecure built-in | Warned about at startup. |
| `OPENBLADE_SERVICE_PASSWORD` | insecure built-in | Warned about at startup. |
| `OPENBLADE_SERVICE_TOKEN` | unset | Required for controller-only routes; startup errors in production if unset. |
| `OPENBLADE_CORS_ORIGINS` | `http://localhost:5173,http://localhost:80` | |
| `OPENBLADE_LOGIN_MAX_ATTEMPTS` | `10` | |
| `OPENBLADE_LOGIN_WINDOW_SECONDS` | see source | |
| `OPENBLADE_MAX_UPLOAD_BYTES` | 10 GB | |
| `OPENBLADE_LOG_LEVEL` | `INFO` | `DEBUG` in production is flagged. |

### SFTP gateway

`OPENBLADE_SFTP_ENABLED` (off), `OPENBLADE_SFTP_HOST` (`0.0.0.0`),
`OPENBLADE_SFTP_PORT` (`2222`), `OPENBLADE_SFTP_MAX_SESSIONS` (`10`).
See [FUSE & NAS namespace](fuse-and-nas.md) — the listener is not actually
served.

### Scalar / emulator

`OPENBLADE_SCALAR_URL`, `OPENBLADE_SCALAR_USER` (`admin`),
`OPENBLADE_SCALAR_PASSWORD`, `OPENBLADE_SCALAR_VERIFY_TLS` (`true`),
`OPENBLADE_SCALAR_API_ONLY` (`false`), `OPENBLADE_EMULATOR_URLS`,
`OPENBLADE_EMULATOR_LATENCY_PROFILE` (`instant`),
`OPENBLADE_EMULATOR_LATENCY_ENABLED` (`true`),
`OPENBLADE_EMULATOR_LATENCY_PROFILE_MS`, `OPENBLADE_EMULATOR_IMAGE`,
`OPENBLADE_EMULATOR_LOCAL_IMAGE`, `OPENBLADE_IBLADE_COMPAT_MODE` (`extended`).

### Test-suite only

`OPENBLADE_SCRATCH_BARCODES` (**everything on these tapes will be destroyed**),
`OPENBLADE_FAULT_TESTS`, `OPENBLADE_TEST_DIRTY_UNMOUNT`,
`OPENBLADE_PERF_RESULTS_FILE`.

### Traps

- `OPENBLADE_DB_PATH` appears in the Dockerfiles and **is read by no code**. The
  real setting is `OPENBLADE_DB_URL`.
- The Dockerfiles set `OPENBLADE_BACKEND=simulator`, which is **not a valid
  value** — it falls back to `mock`. Harmless, but do not copy it.
- `OPENBLADE_VERSION` is a Python constant, not an environment variable.
- Two variables are **not** `OPENBLADE_`-prefixed: `I3_TIMING_PROFILE`
  (simulator load/unload/move timings — `instant`/`realistic`/`hardware`) and the
  `EMULATOR_LATENCY_*` fallbacks.

---

## Related

- [Hardware bring-up](hardware-bring-up.md)
- [Safety model](safety-model.md)
- [Troubleshooting](troubleshooting.md)
- `docs/hardware-setup.md` · `docs/runbooks/mhvtl-rehearsal.md`
