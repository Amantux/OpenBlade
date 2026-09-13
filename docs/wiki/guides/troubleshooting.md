# Troubleshooting — symptoms to causes

Organised by **what you see**, because that is what you have. Every hardware
entry below is a failure that actually occurred during the mhvtl rehearsal
(`docs/runbooks/mhvtl-rehearsal.md`) — none of it is hypothetical.

---

## Discovery and devices

### No `mediumx` row in `lsscsi -g`

```
[14:0:1:0]   tape    IBM      ULT3580-TD8      HB81  /dev/st1  /dev/sg2
[14:0:2:0]   tape    IBM      ULT3580-TD8      HB81  /dev/st2  /dev/sg3
```
…and no changer line.

OpenBlade classifies devices purely by the `lsscsi` type string: `mediumx` is the
changer, `tape` is a drive. No `mediumx` means the changer is not visible to the
host at all, and OpenBlade will fail with `No tape changer was discovered`.

Causes, in order of likelihood:

1. **The i3 exposes its medium changer through a drive (LUN 1 bridging), and the
   partition's control-path drive is not the one you cabled.** Fix in the i3 web
   UI.
2. **FC zoning or `multipathd` is eating the LUN.** Blacklist the tape/changer
   devices in `multipath.conf` — `multipathd` will otherwise grab them.
3. Cabling / HBA not in IT mode.

This failure mode is **structurally absent on mhvtl**, so a green rehearsal tells
you nothing about it. Expect to meet it for the first time at the library.

### Kernel module taints on `modprobe mhvtl`

```
mhvtl: loading out-of-tree module taints kernel.
mhvtl: module verification failed: signature and/or required key missing
```

Both messages are **expected and harmless** for an unsigned out-of-tree module.
They are *fatal* under Secure Boot — check `mokutil --sb-state` first.

### `modprobe` fails, or the build does

You need `linux-headers-$(uname -r)` for the **running** kernel, root, and a real
host (not an unprivileged container).

---

## Device nodes

### `LTFS11253E No index found in the medium` on a tape you know is good

```
LTFS17016E Cannot parse index direct from medium (-21700).
LTFS11253E No index found in the medium.
LTFS11027E Cannot mount volume: medium consistency check failed.
```

**This is a wrong-device-node error masquerading as bad media.** It is the single
most misleading failure in the system, and it is exactly how an operator ends up
throwing away a healthy cartridge.

The LTFS `sg` backend addresses drives as `/dev/sgN`. If it is handed `/dev/stN`
it does not error cleanly — it reads the wrong device and reports the above.

Fix: OpenBlade resolves the mapping via `resolve_sg_device()`, reading
`/sys/class/scsi_tape/<name>/device/scsi_generic/`. If you see this anyway:

- Check what you put in `OPENBLADE_DRIVE_DEVICES`. Use **no-rewind `nst`** nodes.
- **Never derive the `sg` number from the `st` number.** They are allocated
  independently; `st0 -> sg1` and `st2 -> sg4` are routine.
- Remember that the discovery fallback returns the *rewinding* `stN` node
  ([drives & changer ops](drives-and-changer.md)), so set the variable explicitly.

### `sg_inq` exits 50 — "close error: No medium found"

The inquiry itself **succeeded**; only the close failed. Closing a *rewinding*
`/dev/stN` node attempts a rewind, which fails when the drive is empty — and
drives are empty most of the time.

Fix: probe the `sg` node, not the rewinding node.

### `sg_inq is not installed`

```
sg_inq is not installed, so drive serials cannot be read. Install sg3_utils on
the host, or unset OPENBLADE_DRIVE_SERIAL_MAP to run with unverified positional
drive order.
```

`apt install sg3-utils`. Do not take the second option on a multi-drive library.

---

## Drive correlation

### `OPENBLADE_DRIVE_SERIAL_MAP does not match the drives that are attached`

The message names exactly which serials were declared-but-missing and
attached-but-undeclared. Fix the map. **Never bypass the refusal** — it is the
guard working, and bypassing it is how you write to the wrong drive.

### `declares drive element(s) [3] but the changer reports only 3 Data Transfer Element(s) (0..2)`

You copied bay numbers out of the i3 web UI, which numbers drive **bays from 1**.
The map uses **0-based element indices**. Subtract one from each.

### Everything looks correlated but writes land on the wrong cartridge

The serial-map check proves the **set** of attached drives matches what you
declared. It never observes *which serial is in which element*, so a
**transposed or rotated map passes and reports `serials_verified: true`**.

The only cure is the manual check: load a scratch cartridge into element *k*
only, and confirm that only the correlated device reports a tape online. Repeat
per element. See `docs/hardware-setup.md`.

### `sg_inq failed on /dev/sg2 (rc=N)`

Permissions (add the user to the `tape` group; check `OPENBLADE_TAPE_GID` in
Docker), a wrong device path, or the drive is held by another process — LTFS
holds the `st` node while mounted, which is why serial probes use `sg`.

---

## Inventory

### Slot count disagrees with the library header

Import/export (mailslot) elements are kept in a **separate list**, not in
`slots`. If the header says 12 and you count 8, the four mailslots are the
difference.

If they are genuinely missing — a tape in the mailslot is invisible — that was a
real parser bug (`Storage Element 9 IMPORT/EXPORT:Empty` did not match the slot
regex). It is fixed; if you see it again, capture `mtx -f <changer> status`
verbatim and file it.

### A tape is visibly in a drive but `Barcode X not found in inventory`

Historically this was a parser bug: `mtx` is inconsistent about whitespace around
`=`,

```
Data Transfer Element 0:Full (Storage Element 6 Loaded):VolumeTag = OB0007L8
      Storage Element 1:Full :VolumeTag=OB0001L8
```

and the barcode regex required the tight form, so **every tape loaded in a drive
parsed as `barcode=None`**. Fixed.

If you see it now, the likelier cause is that `NoScratchMediaError` is being
raised for a *different* reason — the same exception class carries three
different messages:

| Message | Real cause |
|---|---|
| `No scratch media with sufficient capacity is available` | genuinely no tape with room |
| `Barcode X not found in inventory` | selection/inventory mismatch |
| `No available drives for archive load` | **every drive is busy** |

All three surface as HTTP **503**. Read the detail string.

---

## Mount and unmount

### `LTFS30210I Cannot open device: failed to open /dev/sg2 (16)` on a remount

`16` is `EBUSY`. `umount` returns as soon as the kernel detaches the filesystem,
but the LTFS FUSE process lives on to flush its index and close the drive. Until
it exits, the drive's `sg` node is held.

OpenBlade now polls `/proc` and waits for release, reporting `device_released` in
the unmount result. Any unmount→remount cycle hits this without the wait, and on
a real library so does unload→reload.

If you script around OpenBlade: **check `device_released` before unloading.**

### `TapeMountedError: Drive 0 cannot be unloaded while mounted_rw`

Working as intended — unmount first. Note this guard exists **only in the
simulator**; on real hardware there is no such check
([safety model](safety-model.md)).

### A tape is stuck in `dirty`

`DIRTY` can only transition to `UNMOUNTED`. See
`docs/runbooks/recover-from-dirty-unmount.md`.

---

## Formatting

### `FormatRequiresConfirmationError: Unknown or missing safety token`

One of:

- the token expired (**5-minute TTL**);
- the token was already used (**single-use** — it is deleted on success);
- you are running the CLI against a different database than the one the dry run
  wrote to (see below).

Run `openblade format dry-run` again and use the fresh token promptly.

### `FormatRequiresConfirmationError: Safety token does not authorize this barcode`

The token is bound to one specific barcode. You cannot reuse a token from a
different cartridge.

### `LTFS15029E Tape serial must be 6 characters`

The barcode was passed as `--tape-serial`, which takes exactly six alphanumerics.
Real LTO barcodes are eight (six plus a media-type suffix like `L8`). The barcode
belongs in `--volume-name`. The product code does this correctly; two hand-rolled
copies in the test helpers did not, and were fixed.

### The CLI printed a 20-line traceback

Only `openblade hardware connect-i3` has a curated error handler. Every other
command lets exceptions escape as a Rich traceback. **The last line is the real
message.** The refusal worked; the presentation is poor.

---

## Jobs and the API

### `POST /archive/` returned `202 Accepted` / `"pending"` but nothing is happening

It already finished. The job runs **synchronously inside the request handler**;
the `202`/`pending` is a hardcoded literal. Poll `GET /jobs/{id}` for the truth.
See [jobs & monitoring](jobs-and-monitoring.md).

### The HTTP client timed out during an archive

Same cause. The connection is held for the entire tape operation. Raise client
and reverse-proxy timeouts.

### `openblade jobs` shows nothing, but the API shows jobs

**The CLI ignores `OPENBLADE_DB_URL`** and always uses
`~/.openblade/openblade.db`. If the server was started with that variable set,
you are looking at two different databases. See [the catalog](the-catalog.md).

### `GET /metrics` returns 404

There is no native metrics endpoint. Prometheus exposition is at
`/aml/system/emulator/latency/metrics/prometheus`, it is **authentication-gated**,
and its counters are in-memory (reset on restart).

### The container reports healthy but nothing works

`docker-compose.yml` health-checks `/health`, which returns a static `{"status":
"ok"}` and checks nothing. Probe **`/readyz`** instead — it requires the database
and library to both be reachable.

### 503 with `NoScratchMediaError`, but you have plenty of tapes

See the table above: the same class covers "no free drive". Also check that
candidate tapes are (a) not `CLN*`, (b) not `exported`, (c) not already in a
*different* volume group, and (d) actually formatted — tape selection does not
check the `formatted` flag, so an unformatted tape is selected and the job then
fails at mount.

### Restore of a sharded file fails or returns wrong data from the CLI

`openblade restore` does **not** do the shard detection that `POST /restore/`
does. Use the API for sharded data. See [restoring](restoring.md).

### `Missing shard catalog entries` (404)

The parent record claims more shards than exist as child records. Either the
archive failed partway and left instances `pending` (they are never promoted —
there is no repair job), or the catalog was partially restored. Check
`GET /catalog/{file_id}/shards`.

---

## Building the rehearsal rig

### LTFS `./configure` fails at `checking for icu >= 0.21`

It probes `icu-config`, removed in ICU 62+. Ubuntu ships `icu-uc`/`icu-i18n`
pkg-config modules instead, and the documented `ICU_MODULE_CFLAGS`/
`ICU_MODULE_LIBS` escape hatch does **not** work — `configure.ac` overwrites both
before the fallback. A pkg-config-backed `icu-config` shim on `PATH` resolves it,
and also lets `--enable-icu-6x` take effect, which matters.

### LTFS `make` dies with 51 errors about `uthash.h`

Clone with `--recurse-submodules`.

### `apt-get install mhvtl-dkms mhvtl-utils` → `Unable to locate package`

No distro package exists. Build from source; `scripts/mhvtl/setup.sh` does it.

---

## Related

- `docs/runbooks/mhvtl-rehearsal.md` — the source of most of this page
- `docs/runbooks/real-i3-bringup-plan.md`
- `docs/runbooks/recover-from-dirty-unmount.md` · `replace-failed-drive.md`
- [Drives & changer ops](drives-and-changer.md) · [Safety model](safety-model.md)
