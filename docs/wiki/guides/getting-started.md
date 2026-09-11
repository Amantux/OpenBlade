# Getting started — simulator vs real

You have a tape library. This page gets you from nothing to a verified
archive-and-restore round trip **without touching it**, and then explains
exactly what has to change before OpenBlade will talk to real hardware.

Read this page first. The order matters: everything else in this wiki assumes
you have done the simulator round trip below.

---

## 1. The two backends

OpenBlade has one control plane and two backends behind it.

| | Simulator (`mock`) | Real (`real`) |
|---|---|---|
| Selected by | nothing — it is the default | `OPENBLADE_BACKEND=real` **and** `OPENBLADE_REAL_HARDWARE_ENABLED=true` |
| Moves media | in a Python dict | `mtx` against a SCSI medium changer |
| Writes tapes | in memory | `mkltfs` / `ltfs` against `/dev/sgN` |
| Can destroy data | no | **yes** |
| Everything in this wiki works | yes | mostly — each page flags the gaps |

**Both variables are required.** Setting only `OPENBLADE_BACKEND=real` leaves
you on the simulator. This is deliberate: the gate is checked in
`require_real_hardware()` (`openblade/hardware/safety.py`), which raises
`RealHardwareDisabledError` unless both hold, and every low-level `mtx`, `sg`
and LTFS wrapper takes the resulting guard object as a required argument. You
cannot reach real hardware without constructing that guard, and you cannot
construct it without both variables.

`OPENBLADE_REAL_HARDWARE_ENABLED` is compared against the literal string
`"true"`. `1`, `yes` and `TRUE` do **not** enable it.

---

## 2. Install

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
```

Python 3.12 is the target — CI, mypy and every Dockerfile assume it, and
`.python-version` pins it. Do not run OpenBlade on 3.10/3.11.

This puts the `openblade` console script on your PATH. Check it:

```bash
openblade --help
```

---

## 3. The simulator round trip

Every command below was run against the simulator backend while writing this
page. Output is real, lightly trimmed.

### 3.1 Create a library

```bash
openblade mock init --slots 8 --drives 2 --cartridges 4
```

```
Initialized mock library with 8 slots, 2 drives, 4 cartridges
```

This wipes and recreates the state under `~/.openblade/` — the mock library
state file and the catalog database. It is the only destructive simulator
command.

The cartridges are barcoded `MCK00001`…`MCK0000N`.

### 3.2 Look at it

```bash
openblade inventory
```

```
            Slots
┏━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Slot ┃ Occupied ┃ Barcode  ┃
┡━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ 1    │ True     │ MCK00001 │
│ 2    │ True     │ MCK00002 │
│ 3    │ True     │ MCK00003 │
│ 4    │ True     │ MCK00004 │
│ 5    │ False    │          │
...
                         Drives
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Drive ┃ Loaded ┃ Barcode ┃ Drive State ┃ Mount State ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 0     │ False  │         │ empty       │ unmounted   │
│ 1     │ False  │         │ empty       │ unmounted   │
└───────┴────────┴─────────┴─────────────┴─────────────┘
```

Slots are 1-based. Drives are **0-based** — that asymmetry is real and it bites
people. See [drives & changer ops](drives-and-changer.md).

### 3.3 Format a tape

Formatting is two-step and refuses without a token. Full explanation in
[formatting tapes](formatting-tapes.md); the short version:

```bash
openblade format dry-run --barcode MCK00001
```

```json
{
  "operation": "format",
  "target": "MCK00001",
  "affected_barcodes": ["MCK00001"],
  "warnings": ["Destructive operation", "Inventory barcode must match confirmation"],
  "is_destructive": true,
  "token": "pQwmEs2ZlP05V4eYhX1JcgJK9gSoTGmpyso0t7ji7YI"
}
```

```bash
openblade format confirm --barcode MCK00001 --token pQwmEs2ZlP05V4eYhX1JcgJK9gSoTGmpyso0t7ji7YI
```

```json
{"success": true, "message": "formatted", "details": {"barcode": "MCK00001", "formatted": true}}
```

The token expires after **5 minutes** and is **single-use** — running the same
`confirm` twice fails the second time. Verified.

### 3.4 Create a volume group and archive into it

```bash
openblade volume-group demo-vg
openblade archive --volume-group demo-vg --path /some/source/dir
```

```json
{"job_id": "d0398cd5-9bae-4c5b-b7f3-8543b5ba7bd3", "status": "completed", "job_type": "archive"}
```

> **The CLI blocks until the archive finishes.** The help text says "Enqueue an
> archive job", but `ArchiveService.enqueue()` calls `run_archive_job()` inline.
> There is no background worker. The HTTP API is the same underneath, but it
> *reports* `202 Accepted` / `"pending"`, which is misleading — see
> [jobs & monitoring](jobs-and-monitoring.md).

### 3.5 Browse the catalog

```bash
openblade catalog /
openblade catalog /demo-vg
```

```
               Catalog /demo-vg
┏━━━━━━━━━━┳━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Name     ┃ Type ┃ Size ┃ Path              ┃
┡━━━━━━━━━━╇━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ note.txt │ file │ 16   │ /demo-vg/note.txt │
└──────────┴──────┴──────┴───────────────────┘
```

Note the catalog path: **the volume group name is the first path segment**, and
the source directory's own name is not in it. `/src/note.txt` archived into
`demo-vg` becomes `/demo-vg/note.txt`.

### 3.6 Restore and check the bytes

```bash
openblade restore --path /demo-vg/note.txt --to /tmp/out/note.txt
cat /tmp/out/note.txt
```

```
hello openblade
```

Restore verifies the SHA-256 before handing you the file. See
[restoring](restoring.md).

### 3.7 Check the job log

```bash
openblade jobs
openblade jobs d0398cd5-9bae-4c5b-b7f3-8543b5ba7bd3
```

That is the whole round trip. If all seven steps worked, your install is good.

---

## 4. The HTTP API

The CLI covers a deliberately small slice. Sharded archive, tape operations,
the NAS surfaces and everything the web UI uses are **API-only**.

```bash
uvicorn openblade.api.main:app --reload
```

Then `http://localhost:8000/docs`. Unauthenticated health probes:

```bash
curl localhost:8000/healthz    # component health: database, library, ltfs
curl localhost:8000/readyz     # ready only if database AND library are OK
curl localhost:8000/version
```

Full route list: [API reference](../reference/api.md) (generated).

> `/health` and `/healthz` are **different endpoints**. `/health` returns a
> static `{"status":"ok"}` and checks nothing. `docker-compose.yml` health-checks
> `/health`, so a container with an unreachable database still reports healthy.
> Probe `/readyz` instead.

---

## 5. Two databases, one host

The CLI's config is built by hand in `openblade/cli/main.py:_default_config()`
rather than by `load_config()`, so **the CLI ignores `OPENBLADE_DB_URL`** and
always uses `~/.openblade/openblade.db`. The API honours the variable.

If you set `OPENBLADE_DB_URL` for the server and then run `openblade jobs`, you
are looking at a different database and it will look empty. This is a real bug,
not a documented design; see [the catalog](the-catalog.md).

---

## 6. Before you point it at real hardware

Do not set the two environment variables yet. In order:

1. Complete the simulator round trip above.
2. Read the [safety model](safety-model.md) — in particular which guards are
   simulator-only and therefore **do not protect real tapes**.
3. Read [drives & changer ops](drives-and-changer.md) and set
   `OPENBLADE_DRIVE_SERIAL_MAP`. With more than one drive, skipping this is how
   you write to the wrong drive.
4. Work through [hardware bring-up](hardware-bring-up.md), which points at
   `docs/runbooks/real-i3-bringup-plan.md` — the ordered obstacle list.
5. Rehearse against mhvtl first (`scripts/mhvtl/setup.sh`). That virtual-library
   pass found six product defects before any real cartridge was at risk; see
   `docs/runbooks/mhvtl-rehearsal.md`.

A dry-run mode sits between simulator and live: with the real backend selected,
`OPENBLADE_HARDWARE_DRY_RUN=true` logs every `mtx`/`ltfs` command line without
executing it. Use it to check your device paths before the first real move.

---

## Related

- [Safety model](safety-model.md)
- [Inventory & barcodes](inventory-and-barcodes.md)
- [Formatting tapes](formatting-tapes.md)
- [Troubleshooting](troubleshooting.md)
- [CLI reference](../reference/cli.md) · [API reference](../reference/api.md)
