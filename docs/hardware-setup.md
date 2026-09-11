# Hardware setup

1. Connect the medium changer and tape drive to the host and confirm they appear in `lsscsi -g`.
2. Identify the changer sg device (`/dev/sgN`) and the tape drive device (`/dev/nstN`).
3. Set both `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true` only for explicit validation sessions.
4. Run `openblade hardware connect-i3` to validate guarded changer discovery and inventory wiring before any live workflow.
5. Run `openblade hardware validate-ltfs --device /dev/nst0 --barcode ABC123L9` and add `--mount-point /mnt/ltfs --exercise-mounts` only when mount capability checks are intended.
6. Keep the application in mock mode until read-only inventory and LTFS validation succeed end-to-end.
7. Document barcode conventions and slot maps before allowing write workflows.

## Always use the no-rewind device node (`/dev/nstN`, never `/dev/stN`)

Linux exposes every tape drive twice: `/dev/stN` **rewinds the tape on every
close**, while `/dev/nstN` leaves the head where it is. LTFS keeps a partitioned
index and data area and closes the device between operations, so pointing it at a
rewinding node silently repositions the tape underneath it and can corrupt the
index or overwrite data. Every OpenBlade instruction, `OPENBLADE_DRIVE_DEVICES`
value, and `--device` flag must name `/dev/nstN`. Tool output (for example the
`/dev/st0` column in `lsscsi -g`) still shows the rewinding node — that is the
tool reporting the kernel's naming, not a device to pass to OpenBlade.

## Containerized hardware mode

Bare-metal in the project venv (Python 3.12) is the recommended bring-up path —
see `docs/runbooks/real-i3-bringup-plan.md`. The default `docker-compose.yml` has
**no device passthrough**, so `OPENBLADE_BACKEND=real` cannot work in a container
without the opt-in override `docker-compose.hardware.example.yml`. Copy it, delete
the drive entries you do not have, and run:

```
cp docker-compose.hardware.example.yml docker-compose.hardware.yml   # then edit
docker compose -f docker-compose.yml -f docker-compose.hardware.yml up -d
```

Read the comments at the top of that file first: it is only for hosts whose
hardware has already been validated bare-metal, and it needs `OPENBLADE_TAPE_GID`
set to the group that owns `/dev/sg*` and `/dev/nst*` on the host, because the
container drops to the unprivileged `openblade` user.

## Multi-drive setup: correlate drives by serial number

A Scalar i3 partition can hold up to three drives (the shipped profile is
`scalar-i3-50-3`). The library numbers them as *Data Transfer Element 0..N-1*;
the host numbers them `/dev/nst0..nstN-1`. **Nothing guarantees the two orders
agree.** With one drive the mistake is invisible; with two or three, a wrong
assumption means the changer loads a cartridge into one drive while OpenBlade
writes LTFS into a different one — the "wrote to the wrong drive" bug.

OpenBlade therefore correlates the two sides explicitly
(`openblade/hardware/correlation.py`):

1. **List the devices in the order you intend** — this is authoritative, and the
   bring-up runbook requires setting it on first contact:

   ```
   OPENBLADE_DRIVE_DEVICES=/dev/nst0,/dev/nst1,/dev/nst2
   ```

   Use the **no-rewind** (`nst`) nodes. If this is unset, OpenBlade falls back to
   SCSI-address discovery order, which is an assumption, not a fact.

2. **Read each drive's serial** — plain `sg_inq` fetches the Unit Serial Number
   VPD page (0x80) by default; no flag is needed (`-o/--only` is what *suppresses*
   it). `openblade hardware connect-i3` reports the same values under
   `drive_correlation`:

   ```
   sg_inq /dev/nst0 | grep 'Unit serial number'
   ```

3. **Declare the mapping** from serial to the library's drive element, reading the
   per-bay serials off the i3 web UI:

   ```
   OPENBLADE_DRIVE_SERIAL_MAP=10WT073820:0,10WT073821:1,10WT073819:2
   ```

   ⚠ **The element index is 0-based** — it is `mtx`'s *Data Transfer Element*
   number, which you can read straight off `mtx -f /dev/sgN status`. The i3 web
   UI numbers drive **bays from 1**. For a three-drive partition:

   | i3 UI drive bay | mtx line | index to use |
   |---|---|---|
   | Drive 1 | `Data Transfer Element 0` | `0` |
   | Drive 2 | `Data Transfer Element 1` | `1` |
   | Drive 3 | `Data Transfer Element 2` | `2` |

At startup every configured device is probed and compared against this
declaration. Any disagreement — a declared serial that is not attached, an
attached drive that is not declared, a duplicate serial, the same device listed
twice, a drive that reports no serial, an element index beyond the changer's
drive count, or a drive element with no host device at all — raises
`DriveCorrelationError` and the backend **refuses to start**. Correlation is not
allowed to guess.

With no map declared, OpenBlade falls back to positional order and logs a
`DRIVE ORDER UNVERIFIED` warning listing the observed serials — copy them from
that line to build the map. `connect-i3` reports
`drive_correlation_serials_verified: false` in that state.

### What the serial check does NOT prove — verify it by hand once

The check compares the **set** of attached serials with the set you declared.
Nothing in this design ever observes which serial is physically in which drive
element, so a declaration whose elements are **transposed or shifted** — exactly
what an off-by-one from 1-based bay numbers produces — passes the check and is
reported as `drive_correlation_serials_verified: true`. That flag means "the
drives attached are the drives you declared", never "element 0 is really the
drive you assigned to element 0".

So on first bring-up, confirm the assignment empirically, once, per drive:

```
mtx -f /dev/sgN load <slot> 0              # load a scratch tape into element 0
mt -f $(...device correlated with element 0...) status   # must show a tape online
mt -f <each other drive device> status                   # must show no tape
mtx -f /dev/sgN unload <slot> 0
```

Repeat for each element before any write workflow. Note the load succeeding is
not the check — the check is that **only** the correlated device sees the tape.

### Why not READ ELEMENT STATUS?

The authoritative SCSI route is READ ELEMENT STATUS with the DVCID bit, which
makes the *library* report which drive serial sits in which element — and would
remove the manual step above. `mtx status` does not print serials, and wiring
READ ELEMENT STATUS would add a new binary dependency plus a hex descriptor
parser that could not be validated against any captured real-i3 output. An
operator-declared map whose serial set is machine-checked, plus the one-time
manual confirmation, gets the safety property with code we can test today.
Revisit once real i3 output has been captured.
