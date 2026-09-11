# Inventory & barcodes

Inventory is OpenBlade's answer to "what cartridges do I have and where are
they". Everything destructive is addressed **by barcode**, so this page also
covers what a valid barcode is and the ways a bad one silently gets accepted.

---

## What inventory means here

An inventory is a snapshot of three things:

- **Slots** — storage elements, numbered from **1**. Each is occupied or empty
  and carries at most one barcode.
- **Drives** — data transfer elements, numbered from **0**. Each reports a
  barcode (or none), a `drive_state` and a `mount_state`.
- **Cartridge states** — a per-barcode state such as `in_slot`, `in_drive`,
  `exported`.

The 1-based slot / 0-based drive asymmetry is real. It is also not the same as
the Quantum i3 web UI, which numbers **drive bays from 1**. The most common
`OPENBLADE_DRIVE_SERIAL_MAP` mistake is copying bay numbers straight out of that
UI — see [drives & changer ops](drives-and-changer.md).

## Reading it

```bash
openblade inventory
```

Or over HTTP:

```bash
curl localhost:8000/inventory/
```

```json
{"library_id": "mock-i3-001", "slots": [{"slot_id": 1, "occupied": true, "barcode": "VOL001L9"}, ...], "drives": [...]}
```

Both were run against the simulator while writing this page.

On the real backend the same call runs `mtx -f <changer> status` and parses it.
Nothing is cached: each call re-reads the library.

---

## Import/export slots are a separate list

`MtxStatus` keeps mailslot (import/export) elements in
`import_export_slots`, **not** in `slots`. The reason is written at the parse
site and is worth internalising:

> folding them into `slots` would, on a full library, make the first *empty*
> slot the operator mailslot — so an unload would eject the cartridge to the
> front panel.

If your slot count looks short compared to what the library header reports, the
mailslots are the difference, not missing tapes. (An earlier version of the
parser genuinely did drop all four I/E slots — a tape in the mailslot was
invisible. Fixed; see `docs/runbooks/mhvtl-rehearsal.md` §3.2.)

---

## Barcodes

The domain type is strict: **exactly 8 characters, `[A-Z0-9]` only**, validated
by `^[A-Z0-9]{8}$`. Lowercase input is upper-cased. Real LTO barcodes fit this
naturally — six characters plus a media-type suffix like `L8`/`L9`.

Two conventions the code applies by prefix:

| Prefix | Meaning | Where enforced |
|---|---|---|
| `CLN*` | cleaning cartridge — never selected for archive | `openblade/jobs/archive.py`, `routes_upload.py` |
| `SCR*` or contains `SCRATCH` | treated as scratch, ordered last | `openblade/nas/planner.py` (planner only) |

The `SCR`/`SCRATCH` heuristic exists **only in the dry-run planner**. The actual
archive engine does not know about it.

### Where validation does not happen

Be aware of these before you write a runbook around them:

- `POST /volume-groups/{name}/assign` passes the barcode string straight through
  with **no validation** — no length check, no upper-casing, no check that the
  tape exists in the library, no `CLN` filter. A typo returns `200` and creates a
  phantom cartridge row in the catalog.
- The same endpoint will happily **move a cartridge out of another volume
  group**, also with a `200`. The archive engine respects existing membership;
  the API does not.
- `SQLite` does not enforce the `String(8)` column width, so an over-long
  barcode persists.

`openblade format dry-run` *does* check the barcode against live inventory and
refuses an unknown one with `BarcodeMismatchError`. That is the one path where a
typo is caught early.

---

## Scanning and cartridge state

`openblade/jobs/inventory.py` creates or updates a `cartridges` row per barcode
found and maintains its `state`. It does **not** assign a volume group, so
freshly scanned tapes land unassigned. They get claimed later, implicitly, by
the first archive that needs a tape — see
[volume groups & pools](volume-groups-and-pools.md).

A cartridge whose state is `exported` is excluded from archive selection and
causes `CartridgeOfflineError` (HTTP 409) on restore.

---

## Capacity numbers are not live

Two things to know before you trust a capacity figure:

1. `cartridges.used_bytes` is written only when a tape is finalised — at a tape
   switch or at the end of an archive job. Mid-job it is stale.
2. On the **real** backend, `remaining_capacity()` reads an in-process dict that
   is seeded to a default capacity with `used_bytes = 0` the first time a
   barcode is seen. A freshly started process therefore believes every real tape
   is empty until it has been mounted and walked. Do not use it as a
   free-space oracle after a restart.

The default capacity constant is 12 GB (`12_000_000_000`) across the mock and
real LTFS backends. That is a simulator-scale number, not an LTO-8 number; the
dry-run planner separately assumes 12 TB. They disagree, and nothing reconciles
them.

---

## Related

- [Volume groups & pools](volume-groups-and-pools.md) — how a tape gets claimed
- [Drives & changer ops](drives-and-changer.md) — element numbering in depth
- [Formatting tapes](formatting-tapes.md)
- [API reference](../reference/api.md) — `inventory`, `cartridges` tags
