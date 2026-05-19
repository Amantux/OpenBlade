# OpenBlade safety model

## Core principle
OpenBlade assumes tape automation is destructive and expensive to recover from. Every write-capable or media-moving operation is guarded by explicit state and policy checks.

## Safety gates
1. **Real hardware gate**: real operations require `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true`.
2. **Format confirmation**: formatting requires an operator-visible dry run, expected barcode, and a valid safety token.
3. **Mount-state unload gate**: unload is rejected unless LTFS state is `unmounted`.
4. **Drive ownership gate**: the job queue prevents two jobs from claiming one drive.
5. **Changer ownership gate**: the queue and simulator changer lock prevent concurrent media moves.
6. **Archive completion gate**: catalog entries are recorded only after write verification and clean unmount.
7. **Source retention gate**: source deletion is never implicit.
8. **Read-only default for hardware**: hardware support is conservative and explicit.

## RealHardwareGuard
`RealHardwareGuard` validates backend mode, enablement flag, and operator acknowledgment before real-hardware wrappers can be constructed or used.

## FormatConfirmation
`FormatConfirmation` binds a barcode to a one-time `SafetyToken`. The token has a TTL and validation fails if the barcode does not match the target media.

## State machines
Domain transition tables enforce legal transitions for cartridges, drives, and mount states. Forbidden transitions raise typed exceptions so tests and callers can distinguish safety failures from generic errors.

## Safe hardware enablement
1. Set `OPENBLADE_BACKEND=real`.
2. Set `OPENBLADE_REAL_HARDWARE_ENABLED=true`.
3. Verify device discovery and inventory in read-only mode first.
4. Use explicit dry-run and confirmation workflows for destructive actions.
5. Keep regression tests green before changing safety behavior.
