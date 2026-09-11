# mhvtl rehearsal rig

A virtual tape library — **1 medium changer + 3 LTO-8 drives + 8 barcoded
slots + 4 I/E slots** — so `tests/hardware/` can exercise the *real* hardware
backend (`openblade/hardware/`) with no physical hardware and no risk.

This is Phase 2 of [`docs/runbooks/real-i3-bringup-plan.md`](../../docs/runbooks/real-i3-bringup-plan.md).
For what the first rehearsal actually found, read
[`docs/runbooks/mhvtl-rehearsal.md`](../../docs/runbooks/mhvtl-rehearsal.md).

> **Not a substitute for the i3.** mhvtl emulates the SCSI command set, not the
> robotics, timing, or firmware quirks of a real Scalar i3. It proves the code
> paths work and the parsers match real tool output. Phases 3 and 4 still apply.

## Quick start

```bash
sudo scripts/mhvtl/setup.sh              # build, configure, start, verify
eval "$(scripts/mhvtl/env.sh)"           # discover device paths, export env
.venv/bin/python -m pytest tests/hardware/ -v -m real_hardware

sudo scripts/mhvtl/reset.sh              # between runs: unload all drives
sudo scripts/mhvtl/teardown.sh           # stop daemons, unload module
```

## The scripts

| Script | What it does |
|---|---|
| `setup.sh` | Idempotent. Installs deps, builds + installs mhvtl from source, applies `patches/`, writes `/etc/mhvtl` config, starts the daemons, formats scratch media, verifies with `lsscsi` + `mtx`. |
| `env.sh` | Prints the `export` block for the suite. **Discovers** device paths — they move between boots. |
| `reset.sh` | Returns every loaded tape to its home slot. Run between test passes. |
| `format-scratch.sh` | LTFS-formats the scratch cartridges. Destructive, by design, and only on the scratch barcodes. |
| `teardown.sh` | Stops daemons, removes drop-ins and device nodes, unloads the module. `--purge` also deletes media and config. |

## The configuration

`config/` is installed verbatim into `/etc/mhvtl`.

| File | Contents |
|---|---|
| `device.conf` | Library 10 (`QUANTUM` / `QUANTUM Scalar`) + drives 11/12/13 (`IBM ULT3580-TD8`, serials `OBLADE_D01..03`). |
| `library_contents.10` | 3 drives, 1 picker, 4 MAP (I/E) slots, 8 storage slots. |
| `mhvtl.conf` | `CAPACITY=8000` (MB). mhvtl's 500 MB default is too small for the 100 MB payloads the suite writes. |

### Media

| Barcode | Slot | Role |
|---|---|---|
| `OB0001L8` – `OB0004L8` | 1–4 | data media |
| `CLN001L8` | 5 | cleaning cartridge (exercises the `CLN` branch in the parsers) |
| `OB0007L8`, `OB0008L8` | 6–7 | **scratch** — the only barcodes destructive tests may format |
| — | 8 | deliberately empty, so inventory has an unoccupied slot |

## Why the library is called "QUANTUM Scalar"

mhvtl picks its medium-changer personality in
`usr/cmd/vtllibrary.c:customise_lu()` by matching the **product id** (not the
vendor id) against `QUANTUM` / `ADIC`. A product id of `Scalar i3` — what a real
i3 actually reports — falls through to the *default* personality, which uses
completely different SCSI element addresses:

| personality | `start_drive` | `start_map` | `start_storage` |
|---|---|---|---|
| `scalar_pm` | `0x0100` (256) | `0x0010` | `0x1000` (4096) |
| `default_smc_pm` | `0x0001` | `0x0300` | `0x0400` (1024) |

We deliberately take the Scalar numbers: rehearsing against them proves the
backend never assumes slots are 0- or 1-based, which is the element-addressing
trap Phase 2 of the bring-up plan asks this rig to exercise.

(`mtx` presents 1-based logical element numbers regardless, so OpenBlade sees
slots 1–12. The raw addresses matter to anything issuing SCSI directly.)

## Three upstream obstacles this rig works around

All three are mhvtl bugs, not OpenBlade bugs. Each is documented at the point it
bites; summarised here so you recognise the symptom on a new host.

**1. `vtllibrary` aborts with heap corruption under the Scalar personality.**
`malloc(): invalid next size (unsorted)`, immediately after
`smc_personality_module_register(): mhVTL - Scalar emulation`. `scalar_pm.c` has
two `snprintf` bounds that exceed their `alloc_vpd()` page (VPD `0x80` writes 25
bytes into 24; VPD `0x83` writes 25 bytes at offset 12 of a 36-byte page), and
both functions free the old page **without storing the new one back into
`lu->lu_vpd[]`**, leaving a dangling pointer. Fixed by
`patches/0001-scalar_pm-fix-vpd-overflow-and-uaf.patch`, which `setup.sh`
applies.

**2. mhvtl's own systemd units stop its daemons from working.** Two independent
problems in `vtltape@.service` / `vtllibrary@.service`:

- `ProtectClock=yes` implies a `DeviceAllow=` rule, and setting *any*
  `DeviceAllow` switches the cgroup device policy from `auto` to **closed** —
  which then denies the daemon its own `/dev/mhvtl<n>` node. Symptom:
  `chrdev_create(): Error creating device node for mhvtl: Operation not
  permitted`, or `Could not open transport for minor NN: Operation not
  permitted`.
- `ProtectKernelTunables=yes` mounts `/sys` read-only, so the daemon cannot
  write `/sys/bus/mhvtl/drivers/mhvtl/add_lu` to register its logical unit.
  Symptom — the nasty one — the daemon reports **`active`** while **no SCSI
  device ever appears**: `Could not open 'add_lu': Read-only file system`.

`setup.sh` writes drop-ins under `/run/systemd/system/` (so they vanish on
reboot and never edit a packaged unit) re-allowing the module's dynamic
character major and turning `ProtectKernelTunables` off.

**3. `lsscsi` prints the `mediumx` row before udev creates its `/dev/sg` node.**
A readiness check that waits only for the row sees a changer whose sg column is
`-`, and `mtx -f -` then fails. `setup.sh` waits for the sg nodes themselves.

## Two traps this rig is specifically here to expose

**Drive order is not device order.** mhvtl hands out `/dev/stN` in daemon
*registration* order, which does not track SCSI target order. Across reboots of
this rig we have seen targets 1/2/3 map to `st0/st1/st2` **and** to `st2/st1/st0`.
`setup.sh` prints the SCSI-address → `/dev/st` → serial correlation on every run
rather than letting anyone assume it. Correlate drives by **serial number**, never
by device numbering — the same instruction Phase 3 gives for the real i3.

**`/dev/sg`, `/dev/st` and `/dev/nst` are not interchangeable.**

- LTFS's `sg` backend addresses drives as `/dev/sgN`. Given `/dev/st0` it reads
  the wrong device and reports `No index found in the medium` — a wrong-device
  error that looks exactly like blank media.
- `sg_inq` against the **rewinding** `/dev/stN` exits 50 (`close error: No medium
  found`) whenever the drive is empty, because closing a rewinding node attempts
  a rewind. The inquiry succeeds; the command still reports failure.
- `stN` and `sgN` numbers are allocated independently. `st0 -> sg1` and
  `st2 -> sg4` are both real pairings from this rig. Never derive one from the
  other; `openblade.hardware.discovery.resolve_sg_device()` reads the mapping
  from sysfs.

## Requirements

- Linux with headers for the **running** kernel (`linux-headers-$(uname -r)`).
- Secure Boot **disabled**, or the unsigned out-of-tree module will not load.
- Root. Not usable from inside an unprivileged container — the module must be
  loaded on the host.
- `lsscsi`, `sg3-utils`, `mtx`, `zlib1g-dev`, `liblzo2-dev`, `build-essential`
  (installed by `setup.sh` unless `SKIP_APT=1`).
- LTFS is **optional but strongly recommended**. Without `mkltfs`/`ltfs` the
  discovery, drive-health and changer suites still run; the LTFS,
  archive/restore, sharded and performance suites do not. See the runbook for
  the build recipe — there is no Ubuntu package.

`setup.sh` env overrides: `MHVTL_SRC` (default `/usr/local/src/mhvtl`),
`MHVTL_REF` (default `master`), `SKIP_APT=1`.
