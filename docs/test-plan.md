# Test plan

## Unit tests
Validate domain models, barcode validation, state transitions, and safety policy objects.

## Integration tests
Exercise the simulator library and LTFS backend together, including load/unload, mount/unmount, formatting, read/write, capacity, and changer contention.

## End-to-end tests
Run archive and restore through the same services used by the CLI and API.

## Frontend regressions
Run `cd frontend && npm run test` for Vitest coverage over auth state, active-library scoping, and IE station flows, then `cd frontend && npm run build` to confirm production compilation.

## Property tests
Check cartridge uniqueness and non-negative capacity across generated operation sequences.

## Fault tests
Verify injected mount, write, and capacity faults surface as typed errors.

## Safety regressions
Prove the default config blocks real hardware, formatting requires confirmation, and unload-while-mounted is rejected.

## Backend contract suite

`tests/contract/` (`make test-contract`, marker `contract`) runs one behavioural
contract against every library/LTFS pairing, using only the `LibraryBackend` /
`LTFSBackend` Protocols: inventory shape, load/unload round-trip, double-load
refusal, barcode lookup consistency, RW/RO mount + write + read + checksum, the
unload-while-mounted safety gate, and archive → restore byte equality (sharded and
single-job) through `InventoryService`.

| Pairing | Library | LTFS | Runs |
|---|---|---|---|
| `sim+sim` | `MockLibraryBackend` | `MockLTFSBackend` | always |
| `emulator+sim-ltfs` | `ScalarHttpLibraryBackend` over in-process ASGI (`openblade.api.main:app`) | `MockLTFSBackend` | always |
| `real+real` | `get_library()` | `get_ltfs()` | only with `OPENBLADE_REAL_HARDWARE_ENABLED=true` (marker `real_hardware`) |

Known contract violations are `xfail(strict=True)` with the defect's file:line, so a
fix flips them to XPASS and fails the run until the marker is removed.

**Rule: a new backend must pass this suite before it may be selected via
`OPENBLADE_BACKEND`.** Add it as a named pairing in `tests/contract/conftest.py`.

## Protocol tests
Run the SMB and NFS protocol rig with `make protocols-up && make test-protocols && make protocols-down` (tests are marked `protocols`, live in `tests/e2e/protocols`, and skip with a capability-naming reason when the rig is unavailable). They are not part of the default suite; `.github/workflows/nas-protocols.yml` runs them nightly and on manual dispatch, and CI records the results. The rig uses a hydrator shim to simulate tape recall, so it does not cover the real FUSE hydrator. See `docs/wiki/guides/fuse-and-nas.md`.

## CI lanes
Lane definitions live in the README section "Layered CI/CD". This section lists which tests each lane runs.

- **PR (`ci.yml`, required through `ci-gate`).**
  - `backend-lint`: `ruff check .` and `ruff format --check .`.
  - `backend-typecheck`: `mypy openblade` under strict mode. It blocks merge.
  - `backend-tests`: unit and integration tests.
  - `api-aml-integration`.
  - `i3-smoke`.
  - `frontend-build-test`.
  - `web-flask-smoke`.
  - The ownership check `tools/ci_ownership.py --base origin/master`. It maps every changed path, through `tools/ci_ownership.toml`, to `unit`, `integration`, `safety`, `i3`, `compat`, `frontend`, `docs-only` or `ci-only`. It fails on any changed `openblade/**` or `tests/**` path that has no owner. If you add a new test directory or package, add its glob to the TOML table.
- **Emulator.** `emulator-change-gates.yml` and `i3-emulator-compliance.yml` both run `tests/i3` through the reusable `_emulator-boot-test.yml`, with an `I3_TIMING_PROFILE` input.
  - The timing profiles are `instant`, `realistic`, `hardware`, `normal`, `slow-robotics`, `busy-library`, `intermittent-drive`, `session-expiry`, `rebooting` and `degraded-media`.
  - `tests/i3/test_timing_profiles.py` checks each profile's effect under `VirtualClock`, with no real sleeping.
- **Nightly (`nightly.yml`, not a PR check).** Run the same selections locally with pytest.
  - `slow`: `-m slow`.
  - `stress`: `tests/fault` plus `-m stress`.
  - `fuzz`: `tests/property`.
  - `rebuild`: `tests/unit/test_catalog_rebuild*`.
  - `mutation`: `make mutation` / `tools/mutation_run.sh`. It runs mutmut against the safety-critical modules using `tests/safety` plus the scheduler, drive-lease, recovery and scalar-coordinate unit tests. Surviving mutants are compared with `mutation/baseline.txt`, and any new survivor fails the lane.
  - The pytest markers `stress`, `fuzz`, `rebuild` and `mutation` are registered in `pyproject.toml`.
