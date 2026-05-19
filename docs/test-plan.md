# Test plan

## Unit tests
Validate domain models, barcode validation, state transitions, and safety policy objects.

## Integration tests
Exercise the simulator library and LTFS backend together, including load/unload, mount/unmount, formatting, read/write, capacity, and changer contention.

## End-to-end tests
Run archive and restore through the same services used by the CLI and API.

## Property tests
Check cartridge uniqueness and non-negative capacity across generated operation sequences.

## Fault tests
Verify injected mount, write, and capacity faults surface as typed errors.

## Safety regressions
Prove the default config blocks real hardware, formatting requires confirmation, and unload-while-mounted is rejected.
