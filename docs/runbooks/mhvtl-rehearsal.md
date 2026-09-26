# mhvtl rehearsal — the first real-backend test pass

**Date:** 2026-09-11 · **Outcome:** rig **working**, full `tests/hardware/`
suite green · **Host:** Ubuntu 22.04.5, kernel 5.15.0-174-generic, x86_64,
bare metal, Secure Boot disabled.

This is Phase 2 of `docs/runbooks/real-i3-bringup-plan.md` — which is **not on
`master` yet**; it lives on the `chore/py312-baseline-and-quantum-refs` branch
(commit `1d8c565`), so the links to it below only resolve once that lands. Read
it with `git show 1d8c565:docs/runbooks/real-i3-bringup-plan.md`. Phase 2 is: run the
entire hardware suite against a virtual library, with zero risk, before anyone
cables an i3. It is the first time `openblade/hardware/` has executed against a
real SCSI medium changer, real `mtx`, and real LTFS rather than against sample
strings.

**It found seven product defects.** Five of them would have fired on the very
first i3 session; two of those would have looked like broken hardware rather
than broken code. That is the return on this phase.

The rig itself, its configuration, and the upstream mhvtl bugs it works around
are documented in [`scripts/mhvtl/README.md`](../../scripts/mhvtl/README.md).
This file records what was run and what came out of it.

---

## 1. What was stood up

No distro package exists — `apt-cache search mhvtl` returns nothing on Ubuntu
22.04, and `apt-get install mhvtl-dkms mhvtl-utils` fails with
`E: Unable to locate package`. Built from source:
`github.com/markh794/mhvtl` @ `59f32ee`, version 1.8.0.

The kernel module built and loaded cleanly:

```
mhvtl: loading out-of-tree module taints kernel.
mhvtl: module verification failed: signature and/or required key missing - tainting kernel
scsi host14: mhvtl: version 0.18.43 [20260830-0], opts=0x1
```

(Both taint messages are expected for an unsigned out-of-tree module and are
harmless. They *are* fatal under Secure Boot — check `mokutil --sb-state` first
on the bring-up host.)

Final `lsscsi -g`:

```
[14:0:0:0]   mediumx QUANTUM  QUANTUM Scalar   0108  /dev/sch0  /dev/sg4
[14:0:1:0]   tape    IBM      ULT3580-TD8      HB81  /dev/st1   /dev/sg2
[14:0:2:0]   tape    IBM      ULT3580-TD8      HB81  /dev/st2   /dev/sg3
[14:0:3:0]   tape    IBM      ULT3580-TD8      HB81  /dev/st0   /dev/sg1
```

One changer, three drives, 8 storage slots + 4 I/E slots, barcoded LTO-8 media.
Reproduced by `sudo scripts/mhvtl/setup.sh`.

**LTFS:** also unpackaged. Built `LinearTapeFileSystem/ltfs` @ `7c01479`
(2.4.8.4, LTFS format spec 2.4.0, `sg` backend) into `/usr/local`. Two blockers
worth knowing before you repeat it:

- `./configure` fails at `checking for icu >= 0.21` because it probes
  `icu-config`, removed in ICU 62+; Ubuntu ships `icu-uc`/`icu-i18n` pkg-config
  modules instead. The documented `ICU_MODULE_CFLAGS`/`ICU_MODULE_LIBS` escape
  hatch does **not** work — `configure.ac` overwrites both before the fallback.
  A pkg-config-backed `icu-config` shim on `PATH` resolves it, and also lets
  `--enable-icu-6x` take effect, which matters (`pathname.c` otherwise compiles
  the deprecated `unorm.h` path).
- Clone with `--recurse-submodules`, or `make` dies with 51 errors all rooted in
  a missing `uthash_submodule/src/uthash.h`.

So the LTFS gap is **closed** — every LTFS-dependent test ran for real.

---

## 2. Test results

```bash
sudo scripts/mhvtl/setup.sh
eval "$(scripts/mhvtl/env.sh)"
OPENBLADE_FAULT_TESTS=enabled OPENBLADE_TEST_DIRTY_UNMOUNT=1 \
  .venv/bin/python -m pytest tests/hardware/ -m real_hardware -q
```

### Final state — 48 passed, 0 failed, 8 skipped

| Suite | Pass | Fail | Skip | Notes |
|---|---:|---:|---:|---|
| `test_device_discovery` | 9 | 0 | 0 | |
| `test_changer_operations` | 8 | 0 | 1 | |
| `test_drive_health` | 2 | 0 | 5 | mhvtl does not emulate TapeAlert / some `sg_logs` pages |
| `test_ltfs_operations` | 7 | 0 | 1 | destructive; scratch barcodes only |
| `test_archive_restore` | 5 | 0 | 0 | destructive; full round-trip |
| `test_sharded_operations` | 5 | 0 | 0 | 3 drives, parallel |
| `test_fault_recovery` | 3 | 0 | 1 | `OPENBLADE_FAULT_TESTS=enabled` |
| `test_performance` | 4 | 0 | 0 | |
| `test_catalog_integrity` | 5 | 0 | 0 | |

### First run, before any fix — 24 passed, 8 failed, 24 skipped

The progression is the useful part, because each plateau was a distinct defect:

| Run | Result | Blocked on |
|---|---|---|
| 1 | 24 pass / 8 fail / 24 skip | `NoScratchMediaError: Barcode OB0007L8 not found in inventory` — while the tape was visibly in drive 0 |
| 2 | 24 pass / 8 fail / 24 skip | `RuntimeError: readwrite mount failed` |
| 3 | 44 pass / 1 fail / 11 skip | `Cannot open device: failed to open /dev/sg2 (16)` on remount |
| 4 | 45 pass / 0 fail / 11 skip | — |
| 5 | **48 pass / 0 fail / 8 skip** | fault + dirty-unmount tests enabled |

### Remaining skips — all legitimate

| Skip | Why |
|---|---|
| `test_changer_operations` ×1 | "No higher-level locking backend is available in this repository yet" — structural |
| `test_drive_health` ×5 | mhvtl reports no TapeAlert flags and omits some `sg_logs` diagnostic fields. **An emulator gap, not a code gap — these stay untested until the i3.** |
| `test_ltfs_operations` ×1, `test_fault_recovery` ×1 | Dirty-LTFS fault injection needs process control beyond `SafeRunner` |

---

## 3. Product defects found and fixed

### 3.1 `mtx.py` — drive barcodes were never parsed (critical)

`mtx` is inconsistent about whitespace around `=`:

```
Data Transfer Element 0:Full (Storage Element 6 Loaded):VolumeTag = OB0007L8
      Storage Element 1:Full :VolumeTag=OB0001L8
```

`_BARCODE_RE` required the tight form, so **every tape loaded in a drive parsed
as `barcode=None`**. `find_drive_by_barcode()` was therefore permanently blind,
and archive jobs failed with `Barcode ... not found in inventory` about a tape
sitting in drive 0. The repo's `SAMPLE_MTX_LOADED` uses the tight form on its
drive line, which is why unit tests never caught it.

This would have fired on the i3 the first time a tape was loaded. Fixed; regex
now tolerates optional whitespace.

### 3.2 `mtx.py` — import/export slots silently dropped

`_SLOT_RE` matched `Storage Element (\d+):`, which does not match
`Storage Element 9 IMPORT/EXPORT:Empty`. All four I/E slots vanished from the
inventory, leaving `len(status.slots) == 8` while the header said 12. A tape in
the mailslot was invisible.

The bring-up plan flags i3 I/E slots as a likely regex divergence (Phase 3.4).
It was right.

**They are parsed into a separate list, deliberately.** The obvious fix — put
them in `slots` with an `is_import_export` flag — is wrong, and adversarial
review caught it before it shipped. `SlotState` carries no such flag, so the
distinction is erased at the `library.py` boundary and every consumer would
suddenly see 12 slots where it saw 8. The sharp edge is
`routes_aml_move_medium.py::_first_empty_slot()`, which returns the first
unoccupied slot: on a full library — the normal state of a production
library — the first *empty* element is the operator mailslot, so an unload
would eject the cartridge to the front panel. `routes_aml_library.py` would
also have double-counted I/E slots in `slotsTotal`, on a parity-gated AML
response.

So `MtxStatus.slots` stays "data storage slots", `MtxStatus.import_export_slots`
holds the mailslot elements, `all_slots` merges them for anyone who wants both,
and `slot_count` (the header figure) equals the two together. Nothing
downstream changes behaviour; the information simply stops being discarded.

### 3.3 `ltfs.py` — `device_list()` could never succeed

Three independent problems, all invisible without real LTFS:

- `ltfs -o device_list` writes its **entire output to stderr**; the code parsed
  `result.stdout`, which is empty.
- It **exits 1 even on success**; the code called `raise_on_error()`, so the
  method raised unconditionally.
- The real output format is
  `Device Name = /dev/sg1 (14.0.1.0), Vendor ID = IBM, ... Serial Number = OBLADE_D01`,
  nothing like the fictional `LTFS14001I 0: /dev/st0 (...)` sample the regex was
  written against.

Fixed: parses stdout+stderr, judges success by whether devices were parsed, and
handles both formats. `LTFSDevice` gained a `serial` field — see §4.

### 3.4 `ltfs.py` / `discovery.py` — LTFS was given the wrong device node

The LTFS `sg` backend addresses drives as `/dev/sgN`. `library.py` supplies
`/dev/stN`. Handing LTFS `/dev/st0` does **not** produce a clean error — it
reads the wrong device and reports:

```
LTFS17016E Cannot parse index direct from medium (-21700).
LTFS11253E No index found in the medium.
LTFS11027E Cannot mount volume: medium consistency check failed.
```

which reads as *blank or corrupt media*. On the i3 that is a support incident
where the operator concludes the cartridge is bad.

Added `discovery.resolve_sg_device()` — reads the mapping from
`/sys/class/scsi_{tape,changer}/<node>/device/scsi_generic/` — and applied it in
`format_tape`, `mount_readonly`, `mount_readwrite`. **The mapping must be read,
not derived:** `st` and `sg` numbers are allocated independently, and this rig
routinely produces `st0 -> sg1` and `st2 -> sg4`.

### 3.5 `ltfs.py` — unmount returned before the drive was released

`umount` returns when the kernel detaches the filesystem, but the LTFS FUSE
process lives on briefly to flush its index and close the drive. Until it exits
the drive's sg node is held, and the next mount fails:

```
LTFS30210I Cannot open device: failed to open /dev/sg2 (16).   # EBUSY
```

Any unmount→remount cycle hits this, and on a real library so does
unload→reload. Added `wait_for_ltfs_release()` (polls procfs, bounded 60 s);
`unmount()` now waits and reports `device_released` in its result details.

### 3.6 Test-side defects — `sg_inq` on a rewinding node, and `--tape-serial`

Two bugs in `tests/hardware/` helpers, both of which would have produced false
failures against the i3:

- `_resolve_scsi_path()` normalised `/dev/nst0` → `/dev/st0` for matching and
  then **returned the rewinding node**. `sg_inq /dev/st0` exits 50
  (`close error: No medium found`) whenever the drive is empty, because closing
  a rewinding node attempts a rewind. The inquiry itself succeeds — the command
  still reports failure. Drives are empty most of the time, so this was a
  guaranteed false failure. Now prefers the sg node.
- `_format_tape()` (duplicated in `test_ltfs_operations.py` **and**
  `test_performance.py`) passed the barcode as `--tape-serial`, which takes
  **exactly 6 alphanumeric characters**. Real LTO barcodes are 8 (six plus a
  media-type suffix like `L8`), so this failed every time with
  `LTFS15029E Tape serial must be 6 characters.` The barcode belongs in
  `--volume-name`, which is what the product code already used correctly. Both
  copies fixed.

  *The duplication is the underlying smell:* two hand-rolled copies of the LTFS
  invocation in two test files, both diverging from the product's own correct
  version. Worth collapsing onto `LTFSCommandBackend` later.

### 3.7 `validation.py` — `device_list_ok` was always false

Found by running the Phase 4.1 command against the rig. `validate_ltfs_capabilities`
computed:

```
device_list_ok=any(current.device == device for current in devices)
```

`devices` comes from LTFS, which only ever lists `/dev/sgN`. `device` is
whatever the caller named — and the CLI help for this very command says
*"Tape device path such as /dev/st0"*, while Phase 4.1 of the bring-up plan
passes `/dev/nst0`. The two can never be equal, so the report said:

```json
{"discovered_devices": [ ...three healthy drives with serials... ],
 "device_list_ok": false}
```

A false alarm at the first gate of the bring-up, on a working library, with
the contradicting evidence printed directly above it. Fixed by resolving both
sides to their sg node before comparing. Verified against the rig with all
three spellings — `/dev/st1`, `/dev/nst1`, `/dev/sg2` — all now `true`.

### Regression tests

Every fix ships with one. Parser-level tests live in
`tests/unit/test_hardware_parsers.py` (`TestMtxParserAgainstRealOutput`,
`TestLTFSParserAgainstRealOutput`, `TestResolveSgDevice`); the command-level
ones — which argv is actually built, and how `device_list`/`unmount` behave —
live in `tests/unit/test_hardware_safety.py` against a `RecordingRunner`.

They are anchored on byte-accurate captured output (`SAMPLE_MTX_REAL_SCALAR`,
`SAMPLE_LTFS_DEVICE_LIST_REAL`) rather than on hand-written samples, which is
precisely how the original bugs hid.

**All were mutation-checked** — each fix reverted, the expected tests confirmed
to fail, then restored:

| Mutation | Tests that failed |
|---|---|
| tight `VolumeTag=` regex restored | 1 |
| I/E-blind slot regex restored | 3 |
| `resolve_sg_device` → naive name derivation | 5 |
| real-format branch removed from LTFS parser | 4 |
| `resolve_sg_device` dropped from the 3 LTFS call sites | 4 |
| `device_list` back to `stdout` + `raise_on_error()` | 1 |
| `unmount` judging success on the umount exit code | 2 |
| unreadable `/proc` treated as "released" | 1 |
| `device_list_ok` back to raw string equality | 1 |

The last four exist **because** the first mutation round was not enough. The
initial pass only mutation-checked the *parsers*; adversarial review pointed
out that the plumbing around them — the three `resolve_sg_device` call sites,
`device_list`'s stderr handling, the unmount gate — had no test that would fail
on revert. It also caught a genuinely vacuous assertion: `assert
wait_for_ltfs_release(...)` in the hardware tests passes against a stub that
returns `True` unconditionally, since with no LTFS running the real function
also returns `True` immediately. That is the "returns `[]` for a blocked URL"
pattern the project rules call out by name.

---

### 3.8 What adversarial review changed

Worth recording, because two of these were fixes that *worked* and were still
wrong:

- **I/E slots in the inventory** (§3.2) — reshaped from "flag them" to "keep
  them in a separate list", because the flag was erased downstream and would
  have made the AML unload route eject cartridges to the mailslot.
- **`format-scratch.sh` matched barcodes by substring.** `find_slot()` used an
  unanchored `index($0, want)`, so `format-scratch.sh OB0008L8 ""` formatted
  the requested scratch tape *and then* `OB0001L8`, a data tape — the empty
  argument matched the first cartridge in the library. It also never checked
  what was actually in the drive before running `mkltfs`. Now: barcodes are
  validated as well-formed, matched against the whole VolumeTag, and the drive
  is re-read after loading so the **loaded** cartridge's VolumeTag must equal
  the requested barcode before anything is written. That last step is the
  "positive barcode confirmation" AGENTS.md requires; the first two only decide
  what to load.
- **`reset.sh` unloaded drives without checking for a live LTFS mount** —
  directly against "never unload while LTFS is mounted or dirty", in the one
  new script that issues unloads, and worse because it exists specifically to
  clean up after *failed* runs, which is exactly when a mount is left behind.
  Both it and `format-scratch.sh` now refuse and tell you how to clear it.
- **`unmount()` computed `device_released` and discarded it** — callers marked
  the drive `UNMOUNTED` regardless, so `can_unload_drive()` would green-light
  an unload with LTFS still holding the drive. The wait was a 20-second pause
  that changed nothing. Success now *means* the drive is free.
- **`wait_for_ltfs_release()` failed open** — an unreadable `/proc` (hidepid, a
  container, a different uid) read as "released". For a gate protecting an
  unload, "I could not look" must not mean "safe".
- **Scripts selected the changer as "first `mediumx`"** — Phase 3 cables a real
  i3 to this same host, a real HBA usually enumerates below mhvtl's dynamic
  host number, and a real Scalar i3 reports the same `QUANTUM` vendor string.
  They now key on the rig's unit serial and refuse to guess.
- **`setup.sh`/`teardown.sh` were not as well-scoped as their comments claimed.**
  `/etc/mhvtl` and `/opt/mhvtl` are mhvtl's own defaults, not paths this rig
  invented. Setup now backs up a pre-existing config before overwriting it and
  moves `library_contents.30` aside rather than deleting it; `--purge` removes
  only the media barcodes named in our own `library_contents.10` and restores
  the backup.
- **`MHVTL_REF` defaulted to `master`** — the patch is byte-exact against one
  commit and the results here are from that build, so it is now pinned to the
  full SHA.

---

## 4. Suspected bugs in files owned elsewhere — NOT fixed here

`openblade/hardware/library.py` and `sg.py` are owned by a parallel agent, so
these are reported rather than changed. The two `library.py` findings both
concern `_ordered_drive_devices()`.

**(a) It returns the REWINDING `/dev/stN` node.** It prefers `block_device` over
`sg_device`, and `lsscsi` reports `/dev/st0` in the block column. Every consumer
of `drive_device()` therefore gets a rewinding node. This is the direct cause of
§3.4, and Phase 0.3 of the bring-up plan explicitly calls LTFS-on-a-rewinding-
device a corruption footgun. `ltfs.py` now defends itself with
`resolve_sg_device()`, but the source should prefer `sg_device`, or at minimum
the no-rewind `nst` node.

**(b) Drive ordering is assumed, and the assumption is fragile.** It sorts by
`(host, bus, target, lun)` and indexes by `drive_id`. On this rig that ordering
is **correct** — verified by serial: changer Data Transfer Element 0 is
`OBLADE_D01` at `[14:0:1:0]`, which is the first row in SCSI-address order.

But it is correct by luck of mhvtl's config ordering, and the *device names*
around it are not stable at all. Across reboots of this same rig:

| boot | target 1 | target 2 | target 3 |
|---|---|---|---|
| A | `/dev/st0` | `/dev/st1` | `/dev/st2` |
| B | `/dev/st2` | `/dev/st1` | `/dev/st0` |
| C | `/dev/st1` | `/dev/st2` | `/dev/st0` |

So sorting by SCSI address is defensible and sorting by device name would be
actively wrong — but neither is *verified*. Phase 2 of the plan asks for exactly
this to be proven with ≥2 drives, and the honest answer is: **the ordering
happens to hold on mhvtl, and nothing checks it.** Recommend correlating changer
elements to drives by **serial number** (`sg_inq`, or the new `LTFSDevice.serial`)
and failing loudly on mismatch, before the first multi-drive write to the i3.
`scripts/mhvtl/setup.sh` now prints the correlation table on every run.

### `sg.py` — `parse_sg_inq()` reports every real device as `unknown`

Also owned elsewhere, so reported not fixed. `_DEVICE_TYPE_RE` (sg.py:19)
matches `Device type:`, which is what the module's `SAMPLE_SG_INQ` contains.
Real `sg_inq` (sg3_utils 1.x) prints something else:

```
    length=36 (0x24)   Peripheral device type: medium changer
 Vendor identification: QUANTUM
 Product identification: QUANTUM Scalar
```

`Peripheral device type` does not match `Device type` (lower-case `d`), so the
regex never fires and `device_type` falls through to its `"unknown"` default —
for **every** device. Vendor, product and revision parse correctly, so this is
easy to miss.

Two things need fixing together:

1. the label — accept `Peripheral device type:` as well as `Device type:`;
2. the value — `[^\s]+` would capture only `medium` from `medium changer`. The
   type is a multi-word value and needs to be read to end-of-line.

Impact is diagnostic rather than functional: `ScsiInquiry.device_type` is not
used for any decision, only surfaced in the `connect-i3` report. But that
report is precisely what an operator reads during bring-up, and it currently
labels every device on the bus `"unknown"`:

```json
{"device": "/dev/sg4",
 "inquiry": {"device_type": "unknown", "vendor": "QUANTUM",
             "product": "QUANTUM Scalar", "revision": "0108"}}
```

---

## 5. Where this would block on a different host

The rig needs all of these; each fails loudly with a specific message from
`setup.sh`:

| Requirement | Failure if missing |
|---|---|
| `linux-headers-$(uname -r)` for the **running** kernel | kernel module build fails |
| Secure Boot disabled | `modprobe mhvtl` fails — unsigned out-of-tree module |
| Real host, not an unprivileged container | module cannot be loaded from inside |
| root | `/etc/mhvtl`, `modprobe`, `/dev` nodes |
| `mkltfs` / `ltfs` | LTFS/archive/sharded/performance suites do not run; the rest do |

`setup.sh` is idempotent and `teardown.sh --purge` returns the host to a clean
state, so a failed attempt costs nothing.

---

## 6. What this phase did *not* prove

Being honest about the limits, since the point of a rehearsal is to know what is
still unrehearsed:

- **TapeAlert and drive diagnostics.** 5 skips. mhvtl does not emulate these log
  pages; `openblade/hardware/` code that reads them is still unexercised.
- **Robotics timing and failure modes.** mhvtl moves media instantly and never
  jams. The barcode-reader lag noted in the LTO-7 quirks section, load timeouts,
  and retry paths cannot appear here.
- **Real element addressing.** The rig uses the Scalar personality's element
  addresses deliberately, but `mtx` normalises them to 1-based logical numbers
  before OpenBlade ever sees them. Phase 3.4's "diff real `mtx status` against
  the samples" is still required at the i3.
- **Media compatibility, LTO generation rules, cleaning cycles, WORM.**
- **Control-path/LUN-1 bridging** — the "no `mediumx` appears" failure mode from
  Phase 1 is structurally absent here.
- **Dirty-unmount recovery**, which needs process control beyond `SafeRunner`.

Phases 3 and 4 stand unchanged. What this phase removes is the class of failure
where the *parsers and device plumbing* are wrong — and it found seven of those.

---

## 7. GitHub runner — the weekly canary's own bring-up

`.github/workflows/mhvtl-weekly.yml` reproduces all of §1 on a hosted
`ubuntu-latest` runner every Tuesday. Its first two real runs (2026-09-15
`34930111187`, 2026-09-22 `35688121678`) failed identically; run
`36222156436` is the first green one — **51 passed, 11 skipped, 0 failed in
22.7 s**, LTFS included. Nothing is degraded and nothing is skipped that is not
also skipped on this host (§2).

### The three documented assumptions all HELD

Worth stating plainly, because the workflow header predicted these as the likely
blockers and every one of them was fine:

| Assumption | Runner reality |
|---|---|
| headers for the running kernel exist | `linux-headers-6.17.0-1022-azure  Installed: 6.17.0-1022.22` |
| unsigned out-of-tree module can load | `SecureBoot disabled`, `sig_enforce` = `N`, module built + loaded |
| systemd is PID 1 | `systemctl is-system-running` → `running`, `ps -p 1` → `systemd` |

`mhvtl` registered 1 `mediumx` + 3 `tape`, `mtx status` listed all 12 elements,
and `setup.sh` printed `=== Rig is up ===`. The failure was elsewhere.

### 7.1 `st` and `ch` are not in an Azure kernel's module set (the real blocker)

A **fourth** assumption nobody had written down. mhvtl registers SCSI *targets*;
`/dev/nstN` and `/dev/schN` are created by the kernel's **upper-level** drivers
`st` and `ch`, which is a separate matter from mhvtl entirely. Ubuntu's generic
kernel ships them inside `linux-modules-$(uname -r)` and udev autoloads them —
which is why this host got `/dev/st0-2` and `/dev/sch0` for free and `setup.sh`
never mentioned them. An Azure-tuned kernel puts them in
**`linux-modules-extra-$(uname -r)`, which is not installed on the runner image**:

```
+ modinfo st
modinfo: ERROR: Module st not found.
+ apt-cache policy linux-modules-extra-6.17.0-1022-azure
  Installed: (none)
  Candidate: 6.17.0-1022.22
```

The rig therefore came up with every tape row showing `-` in `lsscsi`'s block
column, an empty `/sys/class/scsi_tape`, and `ls: cannot access '/dev/nst*': No
such file or directory`. `OPENBLADE_DRIVE_DEVICES` is a list of `/dev/nstN`
nodes, so the rig was unusable while looking healthy.

`setup.sh` now `modprobe`s `st` before starting the daemons, apt-installs
`linux-modules-extra-$(uname -r)` and retries if that fails, and **hard-fails**
if it still cannot — no `/dev/nst` means no drive to open, so there is nothing to
degrade to. `ch` is attempted the same way but is only a `NOTE:` when absent:
this rig drives the changer through its `/dev/sg` node with `mtx`.

Confirmed side benefit — the runner's mapping is **non-monotonic**, which is the
independent confirmation §3.4's "must be read, not derived" rule wanted:

```
SCSI_ADDR      BLOCK      SG         NST        SERIAL
2:0:1:0        /dev/st0   /dev/sg2   /dev/nst0  OBLADE_D01
2:0:2:0        /dev/st2   /dev/sg4   /dev/nst2  OBLADE_D02
2:0:3:0        /dev/st1   /dev/sg3   /dev/nst1  OBLADE_D03
```

### 7.2 `sg_inq` needs the `tape` group — and reported the wrong cause

`env.sh` refused with

```
ERROR: found medium changer(s) /dev/sg1 but none reporting unit serial
         'OBLADE_L10'.
```

on a rig whose `device.conf` says exactly `OBLADE_L10`. The `/dev/sgN` nodes are
`0660 root:tape`; the runner user is
`uid=1001(runner) groups=1001(runner),4(adm),100(users),118(docker),999(systemd-journal)`
— **not in `tape`**. `sg_inq` failed with EACCES, whose stderr the serial lookup
swallowed, and an empty serial is indistinguishable from "that is somebody
else's library". On this host the operator was in `tape`, so it never showed.

`rig_sg_inq()` now escalates via `sudo -n` when the node is unreadable, and
`rig_changer` checks for exactly that condition and reports a **permission**
problem as a permission problem. The refusal-to-guess guard is unchanged.

### 7.3 LTFS needed `libsnmp-dev`, not the documented blockers

The `icu-config` shim and `--recurse-submodules` from §1 were both correct and
both worked. The build died earlier than either, on a dependency §1 does not
mention because this host already had it:

```
./configure: line 14786: net-snmp-config: command not found
configure: error: Package requirements (net-snmp >= 5.3) were not met
```

There is no `--disable-snmp`. `libsnmp-dev` provides `net-snmp-config` and the
pkg-config file; with it, LTFS builds, `mkltfs` formats `OB0007L8`/`OB0008L8`,
and the full suite runs. Runtime libraries are now apt-installed **one package
per call**, because noble renamed `libfuse2` → `libfuse2t64` and a single batched
`apt-get ... || true` containing one unknown name installs *nothing* and hides it.

### 7.4 Loudness: the failure that reported itself three steps late

The canary's first red run named the failing step as the pytest one, with
`OPENBLADE_BACKEND: unbound variable`. The real error was `env.sh`'s, 20 lines
earlier, as mere stderr. Cause: `eval "$(scripts/mhvtl/env.sh)"` — **a failing
command substitution does not fail the enclosing command even under `set -e`**,
so the refusal became `eval ""` and the step carried on.

Three changes, and the rule behind them: *if a step's failure means the rig
cannot work, that step must be the red one.*

- Resolution is its own workflow step that checks `env.sh`'s exit status,
  `::error::`s, and exports via `$GITHUB_ENV`. The pytest step additionally
  `${VAR:?}`-guards each variable.
- `setup.sh` identifies the changer with `rig_changer` (serial-keyed) and asserts
  an `/dev/nst` node per drive **before** printing `Rig is up`, so anything that
  would make `env.sh` refuse fails at bring-up instead.
- The `st` driver is a hard failure; only LTFS remains a documented soft cut.

### 7.5 What the canary covers, and what stays degraded

Nothing is degraded on a green run: LTFS builds, so all suites execute. The one
remaining soft cut is unchanged — if the LTFS build ever breaks, the job emits a
`::warning::` and runs the four non-LTFS suites (discovery, changer, drive
health, catalog integrity) rather than going red on a third-party build. The
11 skips are the same legitimate set as §2: 5 TapeAlert/diagnostic pages mhvtl
does not emulate, 4 opt-in fault-injection tests, 1 opt-in dirty unmount, 1
absent higher-level locking backend.

Cold runs build LTFS from source inside the 20-minute step budget; the
`actions/cache` entry is keyed on `LTFS_REF` + runner image identity, and
**Actions cache scoping means a branch's cache is not visible to the default
branch**, so the first scheduled run after any image bump pays the cold cost.

### 7.6 Where this would block on a different runner image

Add to the §5 table, for hosted runners specifically:

| Requirement | Failure if missing |
|---|---|
| `st` in `linux-modules*-$(uname -r)` | no `/dev/nstN`; `setup.sh` fails with the apt hint |
| caller in `tape`, root, or able to `sudo -n` | `sg_inq` EACCES; `rig_changer` says so explicitly |
| `libsnmp-dev` | LTFS `./configure` fails on `net-snmp >= 5.3`; job degrades with a `::warning::` |
