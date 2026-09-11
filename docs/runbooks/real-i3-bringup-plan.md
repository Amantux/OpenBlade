# Real Scalar i3 bring-up — what's in the way

Goal: OpenBlade driving a physical Quantum Scalar i3 with SAS- or FC-attached
LTO drives. The hardware backend (`openblade/hardware/`) is written and
guarded, but as far as the repo shows it has **never touched a real device** —
every parser is validated against sample strings and dry-run output. This plan
orders the obstacles so each phase de-risks the next.

## Phase 0 — baseline before any hardware (this repo, today)

1. **Reconcile the dirty working tree.** Five files are modified and
   uncommitted (`ci.yml`, two emulator workflows, `README.md`,
   `deploy/emulator/ui/app.js`), left over from the Aug 22 session that hit
   its context limit (`.claude/RESUME.md`, checkpoint `caca9241…`). Commit,
   finish, or discard them first — hardware bring-up on an undefined baseline
   makes every later failure ambiguous.
2. **Pick the deployment shape for the bring-up host.** This dev box has no
   HBA; the host at the library will be a different machine. Note that
   `docker-compose.yml` has **no `devices:` passthrough** — the containers
   cannot see `/dev/sg*`/`/dev/nst*` today. For bring-up, run bare-metal in
   the project venv (Python 3.12 — the target host needs it; system 3.10 is
   not supported). Containerized hardware mode is a later, deliberate change.
3. **Fix the st/nst inconsistency now, in docs.** `docs/hardware-setup.md`
   says `/dev/st0`; `tests/hardware/README.md` uses `/dev/nst0`. LTFS on a
   *rewinding* device is a classic corruption footgun — standardize on the
   no-rewind `nst` nodes everywhere before anyone copies a command.

## Phase 1 — physical + host layer (procurement / cabling)

- **HBA.** SAS: a Broadcom/LSI 9300/9400-8e-class SAS3 HBA (IT mode) +
  SFF-8644 external cable to the drive. FC: a 16/32 Gb Emulex/QLogic HBA,
  direct-attach or switch (zone the drive WWPNs). From OpenBlade's point of
  view both end as SCSI devices; the choice changes cabling, not code.
- **Control path.** The i3 exposes its medium changer *through the drive*
  (LUN 1 bridging). Verify in the i3 web UI that the partition's control-path
  drive is the one you cable, or `lsscsi` will show a tape and **no
  `mediumx`** — the first "nothing works" you'll hit.
- **i3 config:** partition created, drives assigned, barcode labels on every
  cartridge (the parsers expect VolumeTags; unlabeled media is a soft-fail
  minefield), I/E station policy known.
- **Host packages:** `sg3_utils`, `mtx`, and an LTFS implementation for IBM
  LTO drives (the i3's drives are IBM). ⚠ `openblade/hardware/ltfs.py`
  assumes specific `ltfs`/`mkltfs` CLI behavior — **verify its flags against
  the exact installed LTFS version before first mount**; LTFS builds drift.
- **Host hygiene:** `st`/`sg` modules loaded; if FC, blacklist tape devices
  from `multipathd` (it will otherwise grab them); user in the `tape` group
  or run as root for bring-up.

## Phase 2 — full rehearsal WITHOUT the i3 (can start today, no hardware)

Install **mhvtl** (virtual tape library) on any Linux box and run the entire
`tests/hardware/` suite against it, per its README. This is the single
biggest de-risker: it exercises the *real* backend code path — guard,
discovery, mtx parsing, load/unload, LTFS flow — for the first time ever,
with zero risk. Two specific traps to validate here, both called out in the
test README's quirks section:

- **Element addressing** — real libraries may start slots at 1, not 0.
- **Drive-order correlation** — `RealLibraryBackend` maps mtx
  Data Transfer Element order onto `/dev/nst*` order
  (`_ordered_drive_devices`). With ≥2 drives this assumption is the classic
  "wrote to the wrong drive" bug; prove it with a 2-drive mhvtl config, and
  at the i3 verify per-drive serial numbers (`sg_inq`) against the library's
  drive list rather than trusting ordering.

## Phase 3 — first contact with the i3: read-only only

```
OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true \
OPENBLADE_CHANGER_DEVICE=/dev/sgN OPENBLADE_DRIVE_DEVICES=/dev/nst0[,...]
```
Set devices **explicitly** — don't trust auto-discovery on first contact.

1. `lsscsi -g` shows `mediumx` + `tape` rows.
2. `openblade hardware connect-i3` (guarded discovery + inventory wiring).
3. `pytest tests/hardware/test_device_discovery.py test_drive_health.py`
   (non-destructive).
4. Diff real `mtx status` output against the samples in
   `openblade/hardware/mtx.py` — i3 I/E slots, cleaning tapes (`CLN…`),
   and barcode suffixes are where the regexes will diverge if they do.
5. Robotics transport stays `scsi`. The `scalar_http` Web Services backend is
   **Milestone-1 / read-only** — `moveMedium` and drive correlation are
   explicitly not implemented — so it cannot move media yet; treat it as a
   parallel read-path check at most.

## Phase 4 — guarded writes, scratch media only

1. `openblade hardware validate-ltfs --device /dev/nst0 --barcode <scratch>`,
   then `--exercise-mounts`.
2. Format one tape through the dry-run + one-time-token flow.
3. Archive/restore round-trip on `OPENBLADE_SCRATCH_BARCODES` tapes only.
4. Only then: multi-drive/sharded tests, fault tests
   (`OPENBLADE_FAULT_TESTS=enabled`), performance baselines.

Buy 2–3 dedicated scratch cartridges of the right generation for the drives
(LTO-8 drive: L8 media, or L7 read-compat rules apply) and never put a
data-bearing tape in the library until Phase 4 is fully green.

## Known unknowns (expect to spend time here)

| Risk | Where it bites | Mitigation |
|---|---|---|
| LTFS CLI flag drift | first mount/format | verify installed version vs `ltfs.py` assumptions before Phase 3 |
| Element numbering off-by-one | first load command | mhvtl rehearsal + read-only diff |
| Drive order ≠ device order | multi-drive writes | serial-number correlation, not ordering |
| Control path not on cabled drive | nothing appears | i3 UI partition check |
| multipathd steals FC tape LUNs | FC only, discovery | blacklist before cabling |
| `scalar_http` can't move media | if webservices transport chosen | use `scsi` transport for v1 |
| compose lacks device passthrough | containerized deploy | bare-metal venv for bring-up |
