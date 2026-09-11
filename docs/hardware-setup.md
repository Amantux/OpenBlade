# Hardware setup

1. Connect the medium changer and tape drive to the host and confirm they appear in `lsscsi -g`.
2. Identify the changer sg device and tape drive device.
3. Set both `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true` only for explicit validation sessions.
4. Run `openblade hardware connect-i3` to validate guarded changer discovery and inventory wiring before any live workflow.
5. Run `openblade hardware validate-ltfs --device /dev/st0 --barcode ABC123L9` and add `--mount-point /mnt/ltfs --exercise-mounts` only when mount capability checks are intended.
6. Keep the application in mock mode until read-only inventory and LTFS validation succeed end-to-end.
7. Document barcode conventions and slot maps before allowing write workflows.

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

At startup every configured device is probed and compared against this
declaration. Any disagreement — a declared serial that is not attached, an
attached drive that is not declared, a duplicate, a drive that reports no serial,
or an element index beyond the changer's drive count — raises
`DriveCorrelationError` and the backend **refuses to start**. Correlation is not
allowed to guess.

With no map declared, OpenBlade falls back to positional order and logs a
`DRIVE ORDER UNVERIFIED` warning listing the observed serials — copy them from
that line to build the map. `connect-i3` reports
`drive_correlation_verified: false` in that state.

### Why not READ ELEMENT STATUS?

The authoritative SCSI route is READ ELEMENT STATUS with the DVCID bit, which
makes the *library* report which drive serial sits in which element. `mtx status`
does not print serials, and wiring READ ELEMENT STATUS would add a new binary
dependency plus a hex descriptor parser that could not be validated against any
captured real-i3 output. An operator-declared map that is machine-verified
against live `sg_inq` gives the same safety property with code we can test today.
Revisit once real i3 output has been captured.
