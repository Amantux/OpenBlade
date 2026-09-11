# Safety model (for operators)

OpenBlade assumes tape automation is destructive and expensive to recover from.
This is the operator's condensed version of `docs/safety.md`, with each gate
marked by **where it is actually enforced** — because three of the eight gates in
that document are weaker than they read, and one is not implemented at all.

Trust the column on the right, not the prose.

---

## The gates at a glance

| # | Gate | Where enforced | Verdict |
|---|---|---|---|
| 1 | **Real-hardware gate** | `require_real_hardware()`; every `mtx`/`sg`/LTFS wrapper requires the resulting guard object | ✅ solid |
| 2 | **Format confirmation** | `FormatService.confirm()` (token) + orchestrator `confirmed_format` flag | ✅ on the documented paths — ⚠️ `POST /ltfs/format` bypasses both |
| 3 | **Mount-state unload gate** | `MockLibraryBackend.unload()` — **simulator only** | ⚠️ absent on real hardware |
| 4 | **Drive ownership gate** | claimed to be the job queue | ❌ **not implemented** |
| 5 | **Changer ownership gate** | simulator changer lock; orchestrator per-drive/per-barcode locks | ⚠️ partial; the queue half does not exist |
| 6 | **Archive completion gate** | sharded engine: real. Classic engine: weaker than described | ⚠️ partial |
| 7 | **Source retention gate** | no code path deletes a source | ✅ solid |
| 8 | **Read-only default for hardware** | the default is `mock` — no hardware at all | ✅ in effect, ❌ as described |

---

## 1. The real-hardware gate ✅

Two variables, both required:

```bash
OPENBLADE_BACKEND=real
OPENBLADE_REAL_HARDWARE_ENABLED=true
```

`OPENBLADE_REAL_HARDWARE_ENABLED` is compared to the literal string `"true"`.
`1`, `yes`, `on` and `TRUE` do **not** work.

This one is genuinely hard to defeat. `require_real_hardware()` raises
`RealHardwareDisabledError` unless both hold, and returns a `RealHardwareGuard`.
Every low-level wrapper — device discovery, `mtx` load/unload/move, `sg_inq`, and
each LTFS operation — takes that guard as a **required parameter**. You cannot
call them without one, and you cannot construct one without both variables. The
guard re-validates on use.

An intermediate setting: with the real backend selected,
`OPENBLADE_HARDWARE_DRY_RUN=true` logs every command line and executes nothing.

*Caveat:* the `OPENBLADE_ROBOTICS_TRANSPORT=webservices` path builds a Scalar
HTTP backend that is reached after the gate but does not itself take a guard.

## 2. Format confirmation ✅ / ⚠️

Two-phase: a dry run mints a barcode-bound, 5-minute, single-use token; confirm
consumes it. Independently, the orchestrator refuses any format whose
`confirmed_format` flag is not `True`. Full detail and rationale in
[formatting tapes](formatting-tapes.md).

> ⚠️ **`POST /ltfs/format` with `{"barcode": …, "confirm": true}` formats a tape
> with no dry run, no token and no authentication.** Verified against the
> simulator. It satisfies the orchestrator flag itself, and the orchestrator
> mints a `SafetyToken` for itself when none is supplied. If you expose the API
> beyond localhost, block this route.

## 3. The mount-state unload gate ⚠️

The rule — unload only when mount state is `unmounted` and drive state is
`loaded` or `failed` — is enforced in exactly one place:
`MockLibraryBackend.unload()`.

**`RealLibraryBackend.unload()` has no such check.** It calls the changer
directly and records the new mount state afterwards. `docs/safety.md`,
`README.md` and `docs/architecture.md` all state this guarantee without the
qualification.

What does protect real tapes is the unmount path, which waits for the LTFS FUSE
process to release the device before reporting success, and treats "I could not
read `/proc`" as "still held". See
[drives & changer ops](drives-and-changer.md).

**Operator rule:** on real hardware, never script an unload immediately after an
unmount without checking the unmount result's `device_released` field.

## 4. The drive ownership gate ❌

`JobQueue.claim_drive()` is **never called by any production code**. A `JobQueue`
is constructed at startup and handed to the services; none of them use its
ownership methods, and its state never reaches the database.

What actually serialises drive access:

- a process-wide lock around **archive** requests (not restores),
- the orchestrator's per-drive lock for write and format, and per-barcode lock
  for read and verify,
- `DriveScheduler` for sharded jobs — real, but constructed **per HTTP request**,
  so two concurrent requests hold independent lock tables.

## 5. The changer ownership gate ⚠️

`JobQueue.claim_changer()` is likewise never called. The simulator does serialise
the changer with a non-blocking lock (`ChangerBusyError: Changer is already
moving media`). The real backend has no equivalent; it trusts `mtx`.

Load, unload, move and eject take **no orchestrator lock at all**.

## 6. The archive completion gate ⚠️

The **sharded** engine genuinely implements it: every shard is checksum-verified,
then every tape is cleanly unmounted and unloaded with all failures aggregated,
and only then are catalog instances flipped from `pending` to `archived`. A dirty
unmount blocks the commit even though the bytes wrote fine, with the error
`shards written but physical state is unknown (reconcile required)`.

The **classic** engine is weaker: it writes catalog rows per file and, on
failure, deletes rows for files that never reached tape. The doc's phrasing
overstates its atomicity.

## 7. Source retention ✅

No code path deletes an archive source. Deletion is always the operator's
separate act.

## 8. "Read-only default for hardware" ❌ as written

There is no read-only hardware mode flag. The default is `mock` — i.e. no
hardware at all. The effect is safe; the description is not accurate.

---

## The import guard — a lint, not a lock

`openblade/safety/import_guard.py` scans source text for code outside an
allow-list calling tape hardware directly (`ltfs.write_bytes(`, `library.load(`,
`library.unload(`, …) instead of going through the orchestrator.

Understand its limits:

- It is a **static text scan run on demand**, surfaced as one line in
  `GET /safety/check`. Nothing aborts at import time or at call time.
- It does not detect aliasing, multi-line calls, or `getattr` dispatch.
- The allow-list has 40+ entries, several explicitly labelled "legacy files
  pending refactor".

It is a code-hygiene signal for developers, not a runtime protection for your
tapes.

`GET /safety/check` returns three checks: "Tape orchestrator" (hardcoded `ok`),
"Direct hardware guard" (the real scan), and "Destructive action confirmation"
(warns if any of the last 100 tape operations was a failed format).

---

## State machines

Transition tables enforce legal transitions for cartridges, drives and mount
states, and forbidden transitions raise typed exceptions so callers can tell a
safety failure from a generic error. The mount table is the load-bearing one:
`MOUNTED_RW → {UNMOUNTED, DIRTY}` and `DIRTY → {UNMOUNTED}` — you cannot get out
of `DIRTY` except by unmounting.

These are enforced in the simulator's library backend. The real backend tracks
mount state through the same validator but does not re-check unload legality.

---

## Enabling real hardware safely

1. Complete the simulator round trip — [getting started](getting-started.md).
2. Rehearse against mhvtl (`scripts/mhvtl/setup.sh`). That pass found six product
   defects with zero cartridges at risk.
3. Set `OPENBLADE_DRIVE_DEVICES` explicitly (no-rewind `nst` nodes) **and**
   `OPENBLADE_DRIVE_SERIAL_MAP`. Verify with `openblade hardware connect-i3`.
4. Do the manual element-to-drive check described in
   [drives & changer ops](drives-and-changer.md). The serial map proves the
   *set* of drives, not their *order*.
5. Run with `OPENBLADE_HARDWARE_DRY_RUN=true` first and read the logged command
   lines.
6. Only then set both real-hardware variables. Start read-only — with
   `openblade hardware connect-i3`, `openblade hardware validate-ltfs`, and
   `GET /inventory/` against a server started with the real-hardware
   environment.

   > ⚠️ **Do not use `openblade inventory` as your hardware check.** Those two
   > `hardware` sub-commands are the *only* CLI commands that read the real
   > configuration. Every other `openblade` command — `inventory`, `archive`,
   > `restore`, `format confirm` — runs against the simulator regardless of
   > `OPENBLADE_BACKEND`, and reports success. Verified: `openblade inventory`
   > under a full real-hardware environment exits 0 and prints simulator
   > cartridges.
7. Use scratch barcodes for every first destructive test, and keep real data out
   of `OPENBLADE_SCRATCH_BARCODES`.

---

## Related

- [Formatting tapes](formatting-tapes.md)
- [Drives & changer ops](drives-and-changer.md)
- [Hardware bring-up](hardware-bring-up.md)
- `docs/safety.md` (the source document) · `docs/runbooks/safe-format-checklist.md`
