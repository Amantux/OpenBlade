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
