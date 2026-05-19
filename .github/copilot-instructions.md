# OpenBlade Copilot Instructions

## Project intent
OpenBlade is a safety-first tape archive controller. The simulator is the primary development backend and must remain complete enough to exercise archive, restore, inventory, and fault paths without hardware.

## What to optimize for
1. Safety over convenience.
2. Deterministic simulator behavior.
3. Clean separation between backend contracts, simulator code, and real hardware wrappers.
4. Small, typed, test-backed changes.

## Non-negotiable rules
- Do not execute real hardware behavior unless both `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true` are set.
- Treat destructive operations as two-phase actions: dry-run plan first, explicit confirmation second.
- Keep real-hardware wrappers read-only by default unless a safety workflow explicitly authorizes a write path.
- Never use `shell=True` in subprocess calls.
- Never import real-hardware modules from simulator code.
- Preserve state-machine invariants for cartridges, drives, changer state, and LTFS mount state.

## Code expectations
- Python 3.12 with full type hints.
- Use structlog-friendly logging.
- Prefer explicit dataclasses and small service objects.
- Keep API request and response models explicit.
- Add or update tests whenever behavior changes.

## Test expectations
- Unit tests for pure domain logic.
- Integration tests for simulator workflows.
- Safety regressions for forbidden operations.
- Property tests for invariant preservation.
