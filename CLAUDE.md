# CLAUDE.md — OpenBlade

> Project override. Where this contradicts a global ~/.claude/CLAUDE.md, THIS wins.
> A global file describing a Postgres/async-SQLAlchemy "business web app" does NOT
> describe this repo — do not apply its stack, layout, or `make check` assumptions here.

## What this is
Simulator-first controller + emulator for a Quantum Scalar i3 LTO tape library,
mocking iBlade-style file/NAS workflows. Safety and determinism beat cleverness.
Python 3.12 target, FastAPI + Typer + SQLite (aiosqlite), React frontend + Flask NAS UI.

## The dual-identity model (read this first)
`openblade.api.main` is ONE app serving TWO surfaces:
- OpenBlade-native: /inventory /jobs /archive /restore /storage /virtual /catalog
- Quantum AML emulator: /aml/* and /iblade/* (parity-gated — see below)
`OPENBLADE_SCALAR_API_ONLY=true` = emulator-only mode: only matrix-documented
/aml + /iblade endpoints pass through; all other native surfaces return 404.
The shipped i3 emulator IMAGE is built in a SEPARATE repo
(Amantux/openblade-i3-emulator) and consumed here via contract — don't assume the
two share code. See openblade/emulator_contract/README.md.

## Environment / interpreter
- This project targets Python 3.12 (CI, mypy strict, ruff py312, all Dockerfiles).
  `.python-version` pins 3.12. Do NOT relax `requires-python` to match a local 3.10/3.11.
- Work through the project venv, not the system `python3` (which may be 3.10):
    uv venv --python 3.12 .venv && uv pip install -e ".[dev]"   # one-time setup
    .venv/bin/python -m pytest ...                              # run tests
  `make` targets assume the venv interpreter is active/on PATH.
- Still verify dependency behavior against installed source in `.venv/`, not memory.

## Commands (verified — there is NO `make check` target)
- Lint:        make lint            (ruff)
- Tests:       make test | make test-unit | make test-integration
- Full gate:   make all             (lint + test + build)
- One suite:   python3 -m pytest tests/i3 -q
- Frontend:    cd frontend && npm run test && npm run build   (vitest / tsc+vite)
- Run stack:   make up | make emulator-up | make fleet-up
Definition of done = `make all` green + relevant frontend checks + any parity gates.

## Effort tiers (project-specific escalation)
- T1: <10 lines, no interface/wire/schema change → just do it. One-line report.
- T2: normal feature/bug in native surfaces or simulator → implement + test + verify.
- T3 (plan mode first, reviewer subagent before "done") if the change touches ANY of:
  - /aml/* routes, aml_state.py, or emulator_contract/**  (the parity contract)
  - the safety gates (real-hardware enable, format token, unload-while-mounted)
  - the catalog schema, or anything that deletes/erases tape data
  - a cross-repo contract version or the manual matrix scope

## Non-negotiables (full list in AGENTS.md — do not re-derive)
- Simulator is the default backend; never run real hardware unless BOTH
  OPENBLADE_BACKEND=real AND OPENBLADE_REAL_HARDWARE_ENABLED=true.
- Never `shell=True`; never import hardware modules from simulator code.
- Destructive ops are two-phase: dry-run plan → explicit barcode + one-time safety token.
- Never unload while LTFS is mounted or dirty.
- A safety-relevant bug ships with its regression test.

## Big files → split carefully
aml_state.py (~4.8k LOC), routes_aml_system.py (~3.5k), routes_iblade.py (~2.3k) are
god files. For any "split"/"de-god-file" task: leave a re-export façade so the public
surface never moves, commit per domain, and after each move verify member count is
unchanged AND wire names are preserved — AML route paths ARE the wire contract.

## Parity workflow (when touching /aml/* or emulator_contract)
Regenerate, don't hand-edit, generated artifacts:
  python3 tools/emulator_spec/generate_matrix_endpoint_catalog.py
  python3 tools/emulator_spec/generate_iblade_parity_coverage.py --allow-partial
Gates: tests/i3/test_15/16/17_*.py and the emulator-change-gates /
i3-emulator-compliance workflows. Scope is pinned `manual-documented-apis-only`:
anything outside the manual needs an explicit policy/matrix update first.

## Pointers (don't duplicate their content)
- Safety & hardware rules → AGENTS.md
- Per-directory rules → .github/instructions/*.instructions.md
- Layered CI (which checks run for which change) → README.md "Layered CI/CD"
- Architecture → docs/architecture.md
- Emulator/parity boundary → openblade/emulator_contract/README.md
