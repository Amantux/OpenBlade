# OpenBlade Hardware Test Suite

Tests that run against real tape hardware. All tests are skipped unless the required environment variables are set.

## Hardware Requirements

- Minimum: 1x LTO tape drive (LTO-7, LTO-8, or LTO-9 recommended)
- Recommended: 1x tape library/changer with ≥4 slots + ≥2 drives  
- Alternative: mhvtl virtual tape library for basic flow testing
- Host: Linux with sg3_utils, mtx, and LTFS packages installed

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

### With mhvtl emulator
```bash
# Install: sudo apt install mhvtl
# Configure and start mhvtl, then:
OPENBLADE_BACKEND=real OPENBLADE_REAL_HARDWARE_ENABLED=true OPENBLADE_CHANGER_DEVICE=/dev/sg2 OPENBLADE_DRIVE_DEVICES=/dev/nst0 pytest tests/hardware/ -v -m real_hardware -k "not performance"
```

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
