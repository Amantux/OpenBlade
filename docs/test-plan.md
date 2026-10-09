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
