# OpenBlade agent notes

## Mission
Build and maintain a safe, simulator-first tape archive controller.

## Guardrails
- Never bypass the real-hardware safety guard.
- Never format without barcode confirmation and a valid safety token.
- Never unload while LTFS is mounted or dirty.
- Never use `shell=True`.
- Prefer deterministic simulator behavior and add tests with each feature.

## Working style
- Keep simulator and hardware modules separated.
- Preserve strict typing and keep mypy clean.
- Update safety and architecture docs when behavior changes.
