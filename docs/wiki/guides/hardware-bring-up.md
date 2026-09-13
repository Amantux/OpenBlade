# Hardware bring-up

This page is a signpost, not a procedure. The procedure lives in
**[`docs/runbooks/real-i3-bringup-plan.md`](../../runbooks/real-i3-bringup-plan.md)**
and is not duplicated here — one copy, kept current by the people doing the
bring-up.

---

## Read these, in this order

1. **[`docs/runbooks/real-i3-bringup-plan.md`](../../runbooks/real-i3-bringup-plan.md)**
   — the ordered obstacle list, Phase 0 (repo baseline) through Phase 4 (guarded
   writes on scratch media), plus a "known unknowns" section and a ~10-minute
   **first-contact gate** to run at the library.
2. **[`docs/runbooks/mhvtl-rehearsal.md`](../../runbooks/mhvtl-rehearsal.md)** —
   what happened when the hardware suite was first run against a virtual library.
   Six product defects, four of which would have fired on the first i3 session.
   Read it *before* Phase 3; it tells you which failures are yours and which are
   the code's.
3. **[`docs/hardware-setup.md`](../../hardware-setup.md)** — device configuration,
   permissions, and the manual element-to-drive verification procedure.
4. **[`scripts/mhvtl/README.md`](../../../scripts/mhvtl/README.md)** — the virtual
   rig: `setup.sh`, `teardown.sh --purge`, and the upstream bugs it works around.

---

## The one thing to internalise before you start

**Rehearse on mhvtl first.** It costs an afternoon and needs no library.

```bash
sudo scripts/mhvtl/setup.sh
eval "$(scripts/mhvtl/env.sh)"
.venv/bin/python -m pytest tests/hardware/ -m real_hardware -q
```

That pass stands up a real SCSI medium changer, three LTO-8 drives and barcoded
media, and runs the entire hardware suite against real `mtx` and real LTFS with
zero risk. The first time it was done it went 24 pass / 8 fail → 48 pass / 0 fail,
and every plateau was a distinct product defect — a barcode regex that never
matched drive lines, dropped import/export slots, an LTFS device-list parser
written against fictional output, LTFS handed a rewinding device node, and an
unmount that returned before the drive was released.

`setup.sh` is idempotent and `teardown.sh --purge` returns the host to clean, so
a failed attempt costs nothing.

Prerequisites, each of which `setup.sh` fails loudly on:
`linux-headers-$(uname -r)` for the **running** kernel · Secure Boot disabled
(`mokutil --sb-state`) · a real host, not an unprivileged container · root ·
`mkltfs`/`ltfs` if you want the LTFS-dependent suites.

---

## What the rehearsal does **not** prove

Be honest with yourself about the remaining risk. From the rehearsal's own
closing section:

- **TapeAlert and drive diagnostics** — mhvtl emulates neither. That code is
  still unexercised.
- **Robotics timing and failure modes** — mhvtl moves media instantly and never
  jams. Barcode-reader lag, load timeouts and retry paths cannot appear.
- **Real element addressing** — `mtx` normalises elements to 1-based logical
  numbers before OpenBlade sees them. Diffing real `mtx status` against the
  samples is still required at the i3.
- **Media compatibility, LTO generation rules, cleaning cycles, WORM.**
- **Control-path / LUN-1 bridging** — the "no `mediumx` appears" failure is
  structurally absent on the virtual rig.
- **Dirty-unmount recovery.**

What it *does* remove is the class of failure where the parsers and device
plumbing are wrong. That class was six bugs deep.

---

## Before you set the two variables

Everything in this list is covered in detail on other pages. Do not skip it:

- [ ] Simulator round trip complete — [getting started](getting-started.md)
- [ ] mhvtl rehearsal green
- [ ] `OPENBLADE_DRIVE_DEVICES` set explicitly to **no-rewind `nst` nodes** —
      discovery's fallback returns rewinding nodes
      ([drives & changer ops](drives-and-changer.md))
- [ ] `OPENBLADE_DRIVE_SERIAL_MAP` set, and `openblade hardware connect-i3`
      clean. **A correlation refusal is the guard working — fix the map, never
      bypass it.**
- [ ] The manual per-element confirmation done by hand. The serial map proves the
      *set* of drives, not their *order*, and a transposed map passes the
      automatic check.
- [ ] A dry-run pass with `OPENBLADE_HARDWARE_DRY_RUN=true`, command lines read
- [ ] [Safety model](safety-model.md) read — especially which guards are
      simulator-only and therefore do **not** protect real tapes
- [ ] Scratch barcodes chosen, and no real data anywhere near
      `OPENBLADE_SCRATCH_BARCODES`

---

## Related

- [Drives & changer ops](drives-and-changer.md)
- [Safety model](safety-model.md)
- [Troubleshooting](troubleshooting.md) — the failure signatures from the rehearsal
