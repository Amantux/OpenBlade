# Tracked technical debt

Known, quantified debt that is deliberately not blocking CI, with the safe way to
pay it down. Advisory CI jobs (`continue-on-error: true`) still run and report;
they just don't fail the build or block trusted-PR auto-merge.

## Lint / format backlog — PAID DOWN (2026-09-12)

**This section is history.** The backlog described here is cleared; see
[`decisions/2026-09-11-pin-ruff-toolchain.md`](decisions/2026-09-11-pin-ruff-toolchain.md).

What it used to say: ~180 `ruff check` findings + ~160 files needing
`ruff format`, with `A002` (~68 findings) called out as un-fixable because
FastAPI path/query params named `id`/`type`/`filter` are the wire contract.

What actually happened: `A002` was added to the `ignore` list as that entry
recommended, which is why the count fell from ~180 to **13**. Those 13 were then
hand-fixed (12 real fixes + 1 justified `# noqa: SIM222`), and the 176-file
format backlog was swept in a single format-only commit verified AST-identical.
`ruff` is now pinned **exactly** (`ruff==0.15.22` in the `dev` extra), so
`make lint`, the editor hook, and CI enforce the same rules on the same version
instead of whatever each happened to resolve.

Two items remain, both deliberate and both scoped:

- `openblade/assistant/tools.py` is the one file still failing
  `ruff format --check` — the package has a live owner and was left
  byte-identical. It needs one `ruff format` pass from that owner.
- 27 `# noqa` markers reference rules that are **not selected** (`BLE001` x23,
  `S603`/`S404`/`S310` x4), so they are inert documentation. Enabling those
  families was measured, not guessed: `BLE` = 44 findings, `S` = 3,748 (3,655 of
  them `S101`, bare `assert` in tests, which would need a per-file ignore).
  Do not delete those markers as dead code — they record intent for that work.

Rule for new debt of this kind: pin the tool version at the same time you pin
the rule set. An unpinned linter makes "is the tree clean?" a question with a
different answer per machine.

## Type-check backlog (advisory: `backend-typecheck`)

- ~300 `mypy openblade` errors across 51 files, concentrated in the god-file
  modules (`aml_state.py`, `routes_aml_*.py`).
- Pay down **per module**, not in one sweep — annotate one file, keep it green,
  and (eventually) move it to a mypy per-file strict allowlist so it can't
  regress. Brute-forcing all 300 at once is high-regression-risk and unreviewable.

## Why the type-check backlog is still advisory

Hand-fixing 300 type errors across wire-contract code in one sweep is a large,
unreviewable, high-regression change. `backend-typecheck` therefore stays
`continue-on-error: true`. The required checks (`backend-tests`, `i3-smoke`) and
the `operability-gate` remain hard gates.

`backend-lint`'s `continue-on-error` no longer has a justification and should be
removed — the exact workflow diff is in
[`decisions/2026-09-11-pin-ruff-toolchain.md`](decisions/2026-09-11-pin-ruff-toolchain.md);
it needs an owner of `.github/workflows/**` to apply it.
