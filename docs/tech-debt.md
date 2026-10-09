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

```bash
grep -roE "datetime\.utcnow\b" --include=*.py openblade tests tools | wc -l   # 54
grep -rlE "datetime\.utcnow\b" --include=*.py openblade tests tools | wc -l   # 16 files
```

54 references in 16 files, all under `openblade/` (0 in `tests/` and `tools/`).
They include both calls and bare references such as SQLAlchemy column defaults
(`default=datetime.utcnow` in `openblade/catalog/models.py`). The largest
groups are `openblade/catalog/models.py`, `openblade/catalog/repository.py`
and `openblade/nas/protocol_gateway.py`. These return naive datetimes. Migrate
them module by module to `datetime.now(timezone.utc)`, and check every column
or comparison that mixes naive and aware values before you switch it.
`openblade/api/aml_state.py` already uses its own aware `_utcnow()` helper and
is not counted here.

## In-memory job queue

`openblade/jobs/queue.py:23` (`class JobQueue`) keeps jobs, drive owners and the
changer owner in process memory (`dict`s guarded by a `threading.RLock`). It is
created once in `openblade/bootstrap.py:614`. Nothing in `queue.py` persists
anything, so a process restart loses queued and running job records and the
ownership map. Any durable recovery has to come from elsewhere (for example,
drive leases and `openblade/jobs/recovery.py`), not from the queue. The queue
also assumes a single process, so running more than one worker process would
give each one its own independent queue.
