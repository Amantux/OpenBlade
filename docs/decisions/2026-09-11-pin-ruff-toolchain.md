# Pin the ruff toolchain; green the lint baseline

Date: 2026-09-11
Status: accepted
Scope: `pyproject.toml`, ~180 source files (format-only), 13 lint findings

## Problem

`make lint` was red on `master`, and it was red *differently* depending on who ran it.

- `pyproject.toml` declared `ruff>=0.6` in the `dev` extra — a floor, not a pin.
- The project venv carried **ruff 0.15.22**.
- CI's `backend-lint` job runs `pip install -e '.[dev]'`, which resolves whatever
  is newest on PyPI at job time — **0.16.7** as of this date, and something else
  next week.

That is the classic unpinned-ruff drift: three enforcement points (editor hook,
venv, CI) nominally running "ruff" and provably able to disagree. It is also why
the `backend-lint` job carries `continue-on-error: true` and a comment describing
a "~180 lint findings + ~160 files needing format" backlog — a permanently-advisory
lint job is a broken signal, exactly like a permanently-red Security tab.

## Decision 1 — pin `ruff==0.15.22`

Both candidate versions were measured against this tree before choosing:

| | `ruff check .` | `ruff format --check .` |
|---|---|---|
| 0.15.22 (venv) | 13 findings | 176 files |
| 0.16.7 (what CI resolves today) | 13 findings — same rules, same files, same lines | 176 files — **identical set** |

They agree byte-for-byte here, because `[tool.ruff.lint].select` is already
explicit, so the widened *defaults* in 0.16 never apply. Churn is therefore zero
either way, and the tiebreak is environment cost:

- **0.15.22** is already installed in `.venv` and in every worktree that shares
  it. Nothing is reinstalled; CI moves *down* to a version whose output we have
  verified.
- 0.16.7 would force a venv reinstall across every active worktree for no
  behavioural gain, and would itself be stale within weeks.

So: `ruff==0.15.22`, exact. Bump deliberately, in its own commit, after
re-running `ruff check . && ruff format --check .`.

**No workflow change is required for the pin itself.** CI installs ruff *only*
via `pip install -e '.[dev]'`, so pinning the extra pins CI. See "Follow-up" for
the one workflow edit that is still wanted.

## Decision 2 — keep the rule set as-is, and say why

`select = ["E", "F", "I", "B", "UP", "A", "SIM"]` was already explicit; the drift
was purely version-driven, not default-driven. It stays, with a comment recording
that it is deliberately explicit.

One real mismatch was found and is being **deferred, not silently ignored**. The
tree carries `# noqa` markers for rules that are not selected, so they are inert:

| noqa code | count | rule family | selected? |
|---|---|---|---|
| `BLE001` | 23 | `BLE` (blind-except) | no |
| `S603` / `S404` / `S310` | 4 | `S` (bandit) | no |

Enabling them was measured, not guessed:

- `--select BLE` → **44** findings (i.e. 44 blind `except Exception:` sites beyond
  the 23 already annotated).
- `--select S` → **3,748** findings, 3,655 of which are `S101` (bare `assert` in
  tests, which is correct usage and would need a per-file ignore).

Turning either on is a policy change with 44 individual judgement calls about
exception handling in safety-relevant code — that is its own task with its own
review, not a side effect of a toolchain pin. Both are recorded in the
`[tool.ruff.lint]` comment so the next person does not read the stray noqa
markers as dead code and delete them.

## Decision 3 — commit shape

Four scoped commits so `git blame` survives and so the inevitable merge conflicts
resolve one class at a time:

1. **pin** — `pyproject.toml` only.
2. **lint fixes** — 12 findings, hand-fixed. `ruff check --fix` offered **zero**
   safe autofixes on this tree ("4 hidden fixes" were all `--unsafe-fixes`), so
   there is no mechanical-autofix commit; every change was reviewed individually.
3. **noqa annotations** — the 1 finding that is deliberate, with a written reason.
4. **format** — `ruff format .` across 176 files, format-only, zero logic.

## Excluded from this pass

- `openblade/assistant/**` — a live agent owns that package; its files are left
  byte-identical. It contributes **0** `ruff check` findings and **1** file to the
  format backlog (`openblade/assistant/tools.py`), handed to that owner rather
  than reformatted here.
- `.github/workflows/**` — owned by another branch this round.
- `docs/wiki/**`.

## Follow-up (needs a workflow owner)

Once this lands, `backend-lint` is green on a pinned toolchain and should stop
being advisory. The exact edit to `.github/workflows/ci.yml`:

```diff
   backend-lint:
     name: backend-lint
     needs: changes
     if: ${{ github.event_name == 'workflow_dispatch' || needs.changes.outputs.backend == 'true' || needs.changes.outputs.api == 'true' || needs.changes.outputs.simulator == 'true' }}
     runs-on: ubuntu-latest
-    # Advisory: the repo carries a large pre-existing ruff/format backlog (~180
-    # lint findings + ~160 files needing `ruff format`). This job still runs and
-    # reports, but does not fail CI (so trusted-PR auto-merge can proceed). Not a
-    # required check. Tracked for incremental cleanup; new code should still lint.
-    continue-on-error: true
+    # Blocking. The lint backlog was cleared and the toolchain pinned exactly
+    # (`ruff==0.15.22` in the dev extra), so this job and a developer's `make
+    # lint` enforce the same rules on the same version. If it goes red, the tree
+    # is genuinely dirty — do not re-add continue-on-error.
     steps:
       - uses: actions/checkout@v5
       - uses: actions/setup-python@v5
         with:
           python-version: "3.12"
           cache: "pip"
       - run: pip install -e '.[dev]'
       - run: ruff check .
       - run: ruff format --check .
```

`backend-typecheck`'s `continue-on-error` is **unrelated** and must stay — the
~300 mypy errors are still real.
