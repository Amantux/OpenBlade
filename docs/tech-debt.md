# Tracked technical debt

Current, verified state only. Every number below was produced by the command
shown next to it, run from the repo root with the pinned dev toolchain
(`ruff==0.15.22`, `mypy==2.3.0` from the `dev` extra) on 2026-10-09. If you
change a number, re-run the command and paste the new result; do not estimate.

## Lint and format: clean and blocking

| Check | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | `All checks passed!` (0 findings) |
| Format | `ruff format --check .` | `365 files already formatted` |

Both run in the `backend-lint` job of `.github/workflows/ci.yml`. That job has no
`continue-on-error`, and `ci-gate` both lists it in `needs:` and fails on any
result other than `success`/`skipped`, so a lint or format failure blocks merge.

The older note that `openblade/assistant/tools.py` was the one file failing
`ruff format --check` is no longer true:
`ruff format --check openblade/assistant/tools.py` reports `1 file already formatted`.

History of how the backlog was paid down:
[`decisions/2026-09-11-pin-ruff-toolchain.md`](decisions/2026-09-11-pin-ruff-toolchain.md).

## Type check: strict, zero errors, blocking

| Check | Command (as in `ci.yml`) | Result |
| --- | --- | --- |
| Backend | `mypy openblade` (`backend-typecheck`) | `Success: no issues found in 170 source files` |
| Flask UI | `mypy openblade/web_flask` (`web-flask-smoke`) | `Success: no issues found in 4 source files` |

`[tool.mypy]` sets `strict = true`. `backend-typecheck` has no
`continue-on-error`. On this branch it is a required dependency of `ci-gate`:
it is listed under `needs:`, and the gate's aggregate step checks its result.
The old "advisory, ~300 errors" entry is obsolete.

## Remaining `# noqa` markers

Inventory command (counts rule codes, which is the same as counting lines here,
because no marker lists more than one rule):

```bash
grep -rhoE "# noqa: ?[A-Z0-9, ]+" --include=*.py openblade tests tools \
  | sed -E 's/# noqa: ?//' | tr ',' '\n' | tr -d ' ' | grep -v '^$' \
  | sort | uniq -c | sort -rn
```

| Rule | Count |
| --- | --- |
| `BLE001` (blind `except Exception`) | 65 |
| `B009` (`getattr` with a constant) | 7 |
| `SIM222` | 1 |
| `F401` | 1 |
| `A001` | 1 |
| **Total** | **75** (openblade 55, tests 20, tools 0) |

There are no bare `# noqa` markers without a rule code (0 matches for
`# noqa` not followed by `:`). `BLE` is now in the selected rule set
(`[tool.ruff.lint] select`), so the `BLE001` markers are active suppressions,
not inert documentation as an earlier version of this file said. Each marker is
supposed to carry a written reason (project rule). Paying them down means
narrowing each `except Exception` to the typed errors the call can actually
raise.

## `datetime.utcnow` (deprecated since Python 3.12)

Paid down 2026-10-09: every call and SQLAlchemy column default now goes through
`openblade/domain/clock.py::naive_utcnow()` (same naive-UTC value, no
deprecation warning). `openblade/api/aml_state.py` keeps its own aware
`_utcnow()` helper. Verify with:

```bash
grep -rE "datetime\.utcnow\b" --include=*.py openblade tests tools | grep -v domain/clock.py | wc -l   # 0
```

## Job queue (resolved 2026-10-10)

`openblade/jobs/queue.py` is now a façade over the catalog `jobs` table and the
`LeaseStore` (`acquire_drive_lease_at`, changer = pseudo-drive `-1`); claims are
heartbeated during `run_job` and refuse drives pending reconciliation. Remaining
gap: `DriveScheduler` leases and queue claims share one drive-id space by design,
and no production caller uses `claim_drive`/`claim_changer` yet.

## NAS persistence gaps

`ArchivePlanRequest.reserved_bytes` is keyed by barcode while `nas_reservations`
are per pool; mapping one to the other needs a design decision before the planner
can consume the ledger. Scratch-threshold health (`get_scratch_thresholds`) has no
consumer because the catalog has no scratch-cartridge count to evaluate.
`nas_file_spans` has no `pool_id`, so identical relative paths in two pools share
spans. A real NFSv4 ACL protocol scenario needs a Ganesha image built with VFS
ACL support; the shipped Debian package cannot store ACLs.