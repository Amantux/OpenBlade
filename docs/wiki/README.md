# OpenBlade wiki

Operator documentation for OpenBlade — every user-facing function, written for
someone who has the hardware but not the codebase.

Two halves:

- **[Guides](#guides)** are hand-written and explain *why*, not just *how*.
- **[Reference](#reference)** is **generated** from the running code by
  `tools/gen_wiki_reference.py` and diff-checked by a test, so it cannot drift.

---

## Start here

New to OpenBlade → **[Getting started](guides/getting-started.md)**. It takes you
through a verified archive-and-restore round trip on the simulator before
anything touches a real cartridge.

About to connect a real library → **[Hardware bring-up](guides/hardware-bring-up.md)**
and **[Safety model](guides/safety-model.md)**, in that order.

Something is broken → **[Troubleshooting](guides/troubleshooting.md)**.

---

## Guides

### Getting going

| Page | Covers |
|---|---|
| [Getting started](guides/getting-started.md) | Simulator vs real, install, the seven-step round trip, the two databases trap |
| [Inventory & barcodes](guides/inventory-and-barcodes.md) | Slots, drives, element numbering, barcode rules, where validation is missing |

### Everyday operations

| Page | Covers |
|---|---|
| [Formatting tapes](guides/formatting-tapes.md) | The dry-run → token → confirm flow, **why** it refuses without a token, and the one endpoint that bypasses it |
| [Volume groups & pools](guides/volume-groups-and-pools.md) | What a volume group really is, tape selection, why "spillover" does not mean what you think |
| [Archiving](guides/archiving.md) | Simple archive, sharding across drives and tapes, atomic commit, and where the sharding docs are wrong |
| [Restoring](guides/restoring.md) | Full and selective restore, sharded reassembly, checksum verification |
| [Drives & changer ops](guides/drives-and-changer.md) | Load/unload/move, `OPENBLADE_DRIVE_SERIAL_MAP`, the wrote-to-the-wrong-drive story, full env-var table |
| [Jobs & monitoring](guides/jobs-and-monitoring.md) | Job states, why everything is synchronous, health endpoints, metrics |

### Systems and concepts

| Page | Covers |
|---|---|
| [The catalog](guides/the-catalog.md) | What is stored, where the SQLite file lives, what rebuild-from-tape can and cannot recover, backup implications |
| [Safety model](guides/safety-model.md) | The eight gates, condensed — with each one marked by where it is *actually* enforced |
| [FUSE & NAS namespace](guides/fuse-and-nas.md) | What is mountable today (nothing), what the namespace APIs do, the three hydration implementations |

### Hardware

| Page | Covers |
|---|---|
| [Hardware bring-up](guides/hardware-bring-up.md) | Short signpost to `docs/runbooks/real-i3-bringup-plan.md` plus the pre-flight checklist |
| [Troubleshooting](guides/troubleshooting.md) | Symptoms → causes, from real failures on the rehearsal rig |

---

## Reference

Generated. Do not edit by hand.

| Page | Contents |
|---|---|
| [CLI reference](reference/cli.md) | Every `openblade` command and sub-command, with options, arguments, types and defaults |
| [HTTP API reference](reference/api.md) | Every native control-plane operation, grouped by tag, with parameters and response models |

Regenerate after changing the CLI or adding a route:

```bash
python3 tools/gen_wiki_reference.py
python3 tools/gen_wiki_reference.py --check     # exit 1 if stale
```

`tests/unit/test_wiki_reference_generated.py` fails if the committed pages do not
match what the generator produces.

The ~980-operation Quantum AML / iBlade emulator surface is **not** expanded in
the API reference — it is a separate wire contract with its own generated
catalog, which that page links to.

---

## A note on honesty

These pages document what the code does, not what it was meant to do. Where a
function is missing, broken, or weaker than an existing document claims, the
guide says so and points at the discrepancy. Several such notes are marked ⚠️.

If you find a guide and a `docs/` page disagreeing, the guide was written against
the source — but check the source yourself before acting on either.

---

## Not covered here

| Topic | Where |
|---|---|
| Architecture and internals | `docs/architecture.md` |
| Quantum AML / iBlade parity | `openblade/emulator_contract/README.md` |
| Deployment | `docs/deployment.md` |
| Disaster recovery | `docs/disaster-recovery.md` |
| Runbooks (bring-up, dirty unmount, failed drive, safe format) | `docs/runbooks/` |
| Contributor rules | `CLAUDE.md`, `AGENTS.md` |
