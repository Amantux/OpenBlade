# Runbook: hardware CI lanes

Four workflows exercise real (or virtual) tape hardware. None of them runs on a
GitHub-hosted runner against a physical library; the physical lanes need the
self-hosted runner described below.

| Workflow | Trigger | Runs on | Touches cartridges? |
|---|---|---|---|
| `hardware-nightly-readonly.yml` | nightly cron + dispatch | `[self-hosted, i3]` | No — read-only by construction |
| `hardware-library-smoke.yml` | dispatch only | `[self-hosted, i3]` | No — dispatchable subset of the nightly lane |
| `hardware-destructive.yml` | weekly cron + dispatch | `[self-hosted, i3]`, environment `hardware-destructive` | Yes — sacrificial cartridges only |
| `mhvtl-weekly.yml` | weekly cron + dispatch | `ubuntu-latest` (mhvtl VM) | Virtual only — rehearsal of the destructive guard rails |

All three physical lanes share the concurrency group `hardware-rig-i3` with
`cancel-in-progress: false`: only one lane holds the rig at a time, and a
running destructive sequence is never cancelled halfway (that can strand a
cartridge in a drive).

`hardware-library-smoke.yml` was kept rather than deleted so its workflow name
and job id (`register-and-validate`) keep resolving for any branch-protection
rule that references them.

## 1. Register the self-hosted runner

1. On the host attached to the i3 (SAS/FC to the changer and drives), create an
   unprivileged runner user that is in the groups owning `/dev/sg*` and
   `/dev/nst*` (usually `tape` and `disk`). Do not run the runner as root.
2. Repo → **Settings → Actions → Runners → New self-hosted runner**, follow the
   download/`config.sh` steps, and give it the labels **`self-hosted,i3`**
   (`./config.sh --labels i3`; `self-hosted` is implicit). Install it as a
   service (`sudo ./svc.sh install <user>`).
3. In the runner's `.env` file, set the rig's device map so the suites can
   address it: `OPENBLADE_CHANGER_DEVICE`, `OPENBLADE_DRIVE_DEVICES`, and the
   drive map described in [../hardware-setup.md](../hardware-setup.md).
4. Install host tools the suites call: `sg3-utils`, `lsscsi`, `mtx`, LTFS.
5. Add repo secrets `QUANTUM_AML_USER` / `QUANTUM_AML_PASSWORD` (AML login).

## 2. Repository variables

Repo → **Settings → Secrets and variables → Actions → Variables**:

- **`HARDWARE_RIG_AVAILABLE`** = `true` — only once the runner above is online.
  Every physical lane's hardware job has `if: vars.HARDWARE_RIG_AVAILABLE == 'true'`.
- **`SACRIFICIAL_BARCODES`** — comma-separated list of cartridges that may be
  loaded, formatted and overwritten (e.g. `OB0007L8,OB0008L8`). Nothing else may
  ever be touched by the destructive lane.

## 3. Environment approval (settings-only — cannot be committed)

Repo → **Settings → Environments → New environment** `hardware-destructive`:

- **Required reviewers**: at least one person who can physically check the
  library. Every run waits for approval, including the weekly scheduled one.
- Optionally restrict **Deployment branches** to `main`.

Without the environment, GitHub creates it on first use with no protection —
so create it, with reviewers, before setting `HARDWARE_RIG_AVAILABLE`.

## 4. The cartridge allowlist

Before any hardware step, the destructive lane (and its mhvtl rehearsal) runs:

```bash
python -m tools.hardware.allowlist --barcodes "$BARCODES"
```

`BARCODES` is the dispatch input `barcodes` (or all of `SACRIFICIAL_BARCODES`
on a scheduled run). The step exits non-zero — and the job stops — if the list
is empty or names any barcode outside `SACRIFICIAL_BARCODES`. The input is
passed through `env:`, never interpolated into the script. mhvtl falls back to
the scratch cartridges its rig loads (`OB0007L8,OB0008L8`) when the variable is
unset.

Each lane also captures inventory + drive state before and after
(`tools.hardware.snapshot capture`), diffs them, and uploads `snapshots/` as an
artifact even on failure. On the read-only lanes the diff must be empty.

## 5. The destructive sequence

Load/unload → LTFS write/read/checksum → archive + restore → sharded write +
restore → safe fault injection → `/jobs/recovery` must report no interrupted
jobs, released leases or drive mismatches.

LTFS format goes only through the two-phase flow (dry-run plan → explicit
barcode + one-time safety token). `test_mkltfs_formats_tape` is deselected in
this lane because it calls `mkltfs` directly.

### Reboot persistence (manual)

The runner cannot power-cycle the appliance. Once per quarter, after a green
destructive run: reboot the i3 from its web UI, wait for it to come Ready,
dispatch `hardware-library-smoke.yml`, and confirm the snapshot diff against
the pre-reboot `after.json` shows the same cartridges in the same slots.

## 6. What "no rig" looks like — and the no-green-by-skipping rule

Without `HARDWARE_RIG_AVAILABLE=true`, the hardware job shows as **Skipped**
and a small `no-rig-notice` job writes "NOT RUN (neutral)" to the run summary.

**No lane may be reported or treated as green because it skipped.** A skipped
hardware job proves nothing about the appliance. Do not add it as a required
status check that a skip satisfies, do not quote a skipped run as evidence, and
do not "fix" a red lane by unsetting the variable.

`actionlint` needs to know the custom label: add `.github/actionlint.yaml` with
`self-hosted-runner: { labels: [i3] }` if you lint these files locally.
