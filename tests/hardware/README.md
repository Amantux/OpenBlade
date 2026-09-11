# OpenBlade Hardware Test Suite

Tests that run against real tape hardware. All tests are skipped unless the required environment variables are set.

## Hardware Requirements

- Minimum: 1x LTO tape drive (LTO-7, LTO-8, or LTO-9 recommended)
- Recommended: 1x tape library/changer with ≥4 slots + ≥2 drives  
- Alternative: the mhvtl virtual tape library (see below) — the full suite has
  been run green against it, not just "basic flow"
- Host: Linux with `sg3-utils` and `mtx` installed, plus LTFS. LTFS is **not**
  packaged for Ubuntu and must be built from source; see the rehearsal runbook.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| OPENBLADE_BACKEND | Yes | Must be `real` |
| OPENBLADE_REAL_HARDWARE_ENABLED | Yes | Must be `true` |
| OPENBLADE_CHANGER_DEVICE | No | Changer sg device (default: /dev/sg0) |
| OPENBLADE_DRIVE_DEVICES | No | Comma-separated drive devices (default: /dev/nst0) |
| OPENBLADE_SCRATCH_BARCODES | No | Comma-separated barcodes safe to FORMAT |
| OPENBLADE_FAULT_TESTS | No | Set to `enabled` to run destructive fault tests |
| OPENBLADE_PERF_RESULTS_FILE | No | Performance results file (default: `.openblade_perf_results.json`) |

⚠️ **WARNING:** OPENBLADE_SCRATCH_BARCODES must only contain tapes you are willing to have fully formatted and overwritten. All data on these tapes will be destroyed.

## Running Tests

### Full hardware suite
```bash
OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true OPENBLADE_CHANGER_DEVICE=/dev/sg0 OPENBLADE_DRIVE_DEVICES=/dev/nst0,/dev/nst1 OPENBLADE_SCRATCH_BARCODES=VOL001,VOL002 pytest tests/hardware/ -v -m real_hardware
```

### Device discovery only (no tape movement)
```bash
OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true pytest tests/hardware/test_device_discovery.py tests/hardware/test_drive_health.py -v
```

### With the mhvtl virtual library (no physical hardware)

There is **no `mhvtl` distro package** — `apt-get install mhvtl` (or
`mhvtl-dkms` / `mhvtl-utils`) fails with `Unable to locate package` on Ubuntu.
mhvtl has to be built from source, including an out-of-tree kernel module, and
`scripts/mhvtl/setup.sh` does the whole thing idempotently: installs the build
deps, builds and installs a **pinned** mhvtl commit, applies the patches in
`scripts/mhvtl/patches/`, writes `/etc/mhvtl`, starts the daemons and verifies
the rig with `lsscsi` and `mtx`.

```bash
sudo scripts/mhvtl/setup.sh              # build + configure + start + verify
eval "$(scripts/mhvtl/env.sh)"           # DISCOVER device paths, export env
pytest tests/hardware/ -v -m real_hardware

sudo scripts/mhvtl/reset.sh              # between runs: unload all drives
sudo scripts/mhvtl/teardown.sh           # stop daemons, unload the module
```

Do not hardcode `/dev/sgN` / `/dev/nstN` for this rig: mhvtl attaches to
whatever SCSI host number is free, so the device paths move between boots.
`env.sh` discovers them and identifies the changer by unit serial number, so it
can never select a real library attached to the same host.

Requirements and caveats: `linux-headers-$(uname -r)` for the **running**
kernel, Secure Boot disabled (an unsigned out-of-tree module cannot load under
it), and root — the module is loaded on the host, so this does not work from
inside an unprivileged container. LTFS is likewise unpackaged and must be built
from source; without `mkltfs` the discovery, drive-health, changer and
catalog suites still run, while the LTFS, archive/restore, sharded and
performance suites cannot.

See [`scripts/mhvtl/README.md`](../../scripts/mhvtl/README.md) for the rig
layout, media/barcode map and the upstream mhvtl bugs it works around, and
[`docs/runbooks/mhvtl-rehearsal.md`](../../docs/runbooks/mhvtl-rehearsal.md)
for the first full pass (including the LTFS build recipe). The
`mhvtl weekly rehearsal` workflow runs this rig on a schedule as a canary.

## Test Categories

- **test_device_discovery**: Non-destructive enumeration and inquiry
- **test_changer_operations**: Robotic arm movements (requires loaded tapes)
- **test_drive_health**: Drive diagnostics via sg_logs (non-destructive)
- **test_ltfs_operations**: Format and mount operations (**destructive** — uses scratch barcodes)
- **test_archive_restore**: Full round-trip (**destructive** — uses scratch barcodes)
- **test_sharded_operations**: Multi-drive parallel ops (requires ≥2 drives + scratch barcodes)
- **test_fault_recovery**: Fault injection (**destructive** — requires OPENBLADE_FAULT_TESTS=enabled)
- **test_performance**: Throughput benchmarks (requires scratch barcodes, logs to `.openblade_perf_results.json` by default)
- **test_catalog_integrity**: Catalog DB consistency checks

## Known Hardware Quirks

### LTO-7
- Element addresses may start at 1 (not 0) depending on library firmware
- Barcode reader may lag 2-5 seconds after robotics completes

### LTO-8
- Mixed LTO-7/LTO-8 tape in same drive may cause format refusal
- M8 media format requires explicit handling in mkltfs

### LTO-9
- Stricter media compatibility enforcement
- Higher sensitivity to firmware version mismatches
- Longer write windows; LTFS index writes at EOT can take >30s
