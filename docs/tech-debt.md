# Tracked technical debt

Known, quantified debt that is deliberately not blocking CI, with the safe way to
pay it down. Advisory CI jobs (`continue-on-error: true`) still run and report;
they just don't fail the build or block trusted-PR auto-merge.

## Lint / format backlog (advisory: `backend-lint`)

- ~180 `ruff check` findings + ~160 files that `ruff format` would rewrite.
- **Safe to auto-fix now:** `ruff check --fix` clears ~half (import order,
  unused imports, f-strings) with no behavior change — do this in a dedicated,
  isolated PR so the large diff is easy to review.
- **Do NOT blindly fix:** `A002` (~68, "argument shadows a builtin") is mostly
  FastAPI path/query params named `id`, `type`, `filter` — those names are the
  **wire contract**; renaming them breaks `/aml` endpoints. Add `A002` to the
  ruff ignore list (justified) rather than renaming.
- `ruff format` across 160 files should be its own commit, never mixed with a
  behavior change.

## Type-check backlog (advisory: `backend-typecheck`)

- ~300 `mypy openblade` errors across 51 files, concentrated in the god-file
  modules (`aml_state.py`, `routes_aml_*.py`).
- Pay down **per module**, not in one sweep — annotate one file, keep it green,
  and (eventually) move it to a mypy per-file strict allowlist so it can't
  regress. Brute-forcing all 300 at once is high-regression-risk and unreviewable.

## Why advisory, not fixed here

Making CI green by fixing all of the above at once would mean reformatting the
whole repo and hand-fixing 300 type errors touching wire-contract code — a large,
risky change that violates minimal-diff discipline. The required checks
(`backend-tests`, `i3-smoke`) and the `operability-gate` remain hard gates.
