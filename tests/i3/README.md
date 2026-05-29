# OpenBlade — Quantum i3 Test Suite

This test suite validates OpenBlade's control plane against either the built-in
emulated Quantum i3 or a real physical Quantum Scalar i3 tape library.

Default emulator profile:
- 50 slots across 2 bays
- 3 drives (2x LTO-7, 1x LTO-8)
- 30 occupied slots (~60% full)
- Default login: `admin` / `password`

## Quick start (emulator)

```bash
# Run all i3 tests against the running emulator
python3 -m pytest tests/i3/ -m i3 -v

# Fast CI run (no delays)
I3_TIMING_PROFILE=instant python3 -m pytest tests/i3/ -m i3 -q

# Realistic emulator feel (3–35 second op delays)
I3_TIMING_PROFILE=realistic python3 -m pytest tests/i3/ -m i3 -v
```

## Targeting a real Quantum i3

```bash
export I3_TEST_MODE=real
export I3_REAL_HARDWARE_ENABLED=true
export I3_AML_URL=http://192.168.1.50:8082    # your i3's AML HTTP API
export I3_AML_USER=admin
export I3_AML_PASSWORD=yourpassword
export I3_TIMING_PROFILE=hardware             # set automatically in real mode

python3 -m pytest tests/i3/ -m i3 -v --tb=short
```

> **Safety gate:** `I3_REAL_HARDWARE_ENABLED=true` must be set or all real-i3
> tests are skipped automatically. This prevents accidental hardware runs.

## Env vars

| Variable | Default | Description |
|---|---|---|
| `I3_TEST_MODE` | `emulator` | `emulator` or `real` |
| `I3_AML_URL` | `http://localhost:8000` | AML API base URL |
| `I3_AML_USER` | `admin` | AML username |
| `I3_AML_PASSWORD` | `password` | AML password |
| `I3_TIMING_PROFILE` | `instant` (emulator) / `hardware` (real) | `instant` / `realistic` / `hardware` |
| `I3_REAL_HARDWARE_ENABLED` | `false` | Safety gate for real i3 mode |

## Timing profiles

| Profile | tape_load | move | format | Description |
|---|---|---|---|---|
| `instant` | 0s | 0s | 0s | CI — no delays |
| `realistic` | 3s | 1.5s | 8s | Feels like real hardware |
| `hardware` | 35s | 8s | 300s | Real Quantum i3 tolerances |

## Test modules

| File | Description |
|---|---|
| `test_01_auth.py` | Login, session expiry, bad credentials, logout |
| `test_02_inventory.py` | Slot counts, drive states, media list, physical map |
| `test_03_changer.py` | Load/unload/move + state machine assertions + timing |
| `test_04_drives.py` | Drive health, status transitions, cleaning detection |
| `test_05_media.py` | Cartridge lifecycle, pool assignment, state transitions |
| `test_06_operations.py` | Move wizard, mount/unmount, IE door, queue |
| `test_07_ltfs.py` | Format (with realistic delay), mount, browse, unmount |
| `test_08_archive_cycle.py` | Archive → verify → catalog (full timing) |
| `test_09_restore_cycle.py` | Restore → checksum → destination |
| `test_10_fault_scenarios.py` | Drive failure mid-archive, jam, partial restore |
| `test_11_diagnostics.py` | Health, events, firmware, RAS tickets |
| `test_12_multi_library.py` | Library switch, scoped inventory, header routing |
| `test_13_ui_scenarios.py` | Scenario tests matching UI workflows |
| `test_14_emulator_profile.py` | Deterministic default i3 profile invariants |
| `test_15_manual_matrix_contract.py` | Manual-derived endpoint matrix integrity and 5-case minimum checks |

Manual-derived compliance matrix source:
- `openblade/emulator_contract/quantum_i3_rev_h_matrix.json`
- generated via `tools/emulator_spec/build_manual_matrix.py`

## Running from the UI

Navigate to **System & Admin → Test Runner** in the OpenBlade web UI.
Select a target, timing profile, and test modules, then click **Run Tests**.
Output streams live to the browser. Download the JSON report when complete.

## GitHub workflow: real hardware registration + command smoke

Use the **Quantum hardware smoke** workflow (`.github/workflows/hardware-library-smoke.yml`)
to validate a physical Scalar i3/i6 (or similar) against the smoke command set.

The workflow:
1. Optionally registers/updates the target library in OpenBlade (`/api/libraries`).
2. Runs `tests/i3/test_00_command_matrix.py` in real mode.
3. Uploads a JSON compatibility report artifact (`i3-command-matrix`).

Required repository secrets:
- `QUANTUM_AML_USER`
- `QUANTUM_AML_PASSWORD`

Optional secrets for OpenBlade library registration step:
- `OPENBLADE_ADMIN_USER`
- `OPENBLADE_ADMIN_PASSWORD`

Notes:
- `include_motion_tests=true` adds move-command compatibility checks.
- `include_control_plane_checks=true` also checks `/api/libraries` compatibility and
  should be used when `target_aml_url` points at an OpenBlade API endpoint.
