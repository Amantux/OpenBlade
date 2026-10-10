# Persistent drive leases with fencing, and job recovery on restart

Status: accepted 2026-10-09. Roadmap items 7 and 8 (Phase 2), first slice.

## Where the four working copies stand (so nobody re-merges them)

| checkout | branch | relation to `origin/master` |
|---|---|---|
| `/root/ob2-final` | `feat/persistent-drive-leases` | integration branch for this work and the 2026-10-09 fleet (per-item status table in `docs/production-readiness-roadmap.md`) |
| `/root/ob-final` | `integrate/eight-items` | tree-identical to PR #44 squash, already on master |
| `/root/ob-int2` | `integrate/campaign-wiki-assistant` | fully contained in `ob-final` |
| `/root/OpenBlade` | `chore/py312-baseline-and-quantum-refs` | stale (37 behind); its 3 stray commits are already on master by content |

Nothing is left to merge. All new work starts from `origin/master`.

## Problem

`DriveScheduler` is the only thing that stops two tape jobs from driving the
same physical drive, and it is constructed **per request** and held **in
memory**:

- `openblade/api/routes_archive.py:196`, `routes_restore.py:159` and `:234`,
  `openblade/cli/main.py:624`, `:698`, `:838` each do
  `DriveScheduler(num_drives=len(context.library.inventory().drives))`.
- Two API requests (only the archive route has a process-wide lock), or the
  CLI and the API against the same catalog, each get a scheduler that believes
  every drive is free.
- After a crash or restart, nothing knows which drives a dead job held, and
  catalog jobs stay `running` forever.

The roadmap (items 7, 8) asks for DB-backed leases with a monotonic fencing
token and restart recovery. This note is the spec for that slice.

## Decision

### 1. Lease store in the catalog

New table `drive_leases` (SQLAlchemy model in `openblade/catalog/models.py`,
created by `Base.metadata.create_all`; nothing to add to `_migrate_schema`
because it is a new table):

| column | type | notes |
|---|---|---|
| `id` | str pk (uuid4) | lease id |
| `drive_id` | int, not null | scheduler lock key; `UNIQUE` while the lease is live (see below) |
| `job_id` | str, not null, indexed | owning catalog job |
| `barcode` | str(8), not null | cartridge the lease was taken for |
| `physical_drive_id` | int, nullable | mirrors `DriveHandle.physical_drive_id` |
| `fencing_token` | int, not null, unique | monotonic across the catalog |
| `acquired_at` | datetime utc | |
| `heartbeat_at` | datetime utc | updated by `heartbeat()` |
| `expires_at` | datetime utc | `heartbeat_at + ttl`; default ttl 15 min |
| `released_at` | datetime utc, nullable | null = live |

Live-ness is `released_at IS NULL AND expires_at > now`. Enforce "one live
lease per drive" in code under a write transaction (SQLite `BEGIN IMMEDIATE`
via `connection.exec_driver_sql`, Postgres not a target here) — do **not** rely
on a partial unique index, SQLite support for those in SQLAlchemy is uneven.

`fencing_token` = `COALESCE(MAX(fencing_token), 0) + 1` inside the same
transaction. It is never reused, even after release.

Repository API (`CatalogRepository`, keep it small):

```python
def acquire_drive_leases(self, *, job_id, barcodes: list[str], num_drives: int, ttl: timedelta) -> list[DriveLease] | None  # None = not all free, caller waits
def heartbeat_leases(self, lease_ids: list[str], ttl: timedelta) -> None
def release_leases(self, lease_ids: list[str]) -> None
def lease_is_live(self, lease_id: str, fencing_token: int) -> bool
def live_leases(self) -> list[DriveLease]
def release_leases_for_job(self, job_id: str) -> int
```

`DriveLease` is a frozen dataclass in `openblade/domain/models.py`.

### 2. `DriveScheduler` becomes a façade over a lease store

Keep the public shape every caller and ~all tests already use:
`acquire_drives(barcodes, timeout)`, `release_drives(handles)`, `status()`,
`available_count()`, `num_drives`. Add:

- `DriveScheduler(num_drives, *, store: LeaseStore, job_id: str, ttl=...)`.
- `LeaseStore` is a `Protocol` with two implementations:
  `InMemoryLeaseStore` (existing behaviour, used by unit tests that build a
  scheduler directly) and `CatalogLeaseStore` (wraps the repository calls
  above). This is the one new abstraction and it exists because tests and
  production genuinely differ.
- `acquire_drives` keeps the all-or-nothing + timeout semantics; the wait loop
  polls the store (0.25 s) instead of `Condition.wait` when the store is the
  catalog one.
- `DriveHandle` gains `lease_id: str` and `fencing_token: int`. `drive_id`
  stays immutable (existing test pins this).
- New `verify(handle)` → raises `StaleLeaseError` (new typed error in
  `openblade/domain/errors.py`, subclass of the existing domain error base so
  the jobs error sanitizer treats it as typed) when the lease is not live or
  the token differs.
- New `heartbeat(handles)`.

### 3. Fencing at the destructive edges

A fenced-out job (`StaleLeaseError`) must **not** unmount or unload: the drive
may already hold the new lease-holder's mounted tape. It logs the drive as
"physical state unknown" and leaves it for reconciliation. Held leases are
kept alive by a daemon heartbeat thread in the scheduler (every `ttl/3`), so a
single long write cannot expire its own lease.

In `openblade/jobs/sharded_archive.py`, `archive.py`, `restore.py`,
`tree_restore.py`: call `scheduler.verify(handle)` immediately before
`ltfs.mount(..., READ_WRITE)`, before each shard/file write batch, and before
unmount/unload. A stale lease aborts the job with `StaleLeaseError`; the error
path must still attempt the clean unmount/unload **and must not suppress its
errors** (existing rule). Call `heartbeat` after each file/shard write.

### 4. One scheduler per process, bound to the catalog

- `AppContext` (`openblade/bootstrap.py`) gets a `lease_store: LeaseStore`
  built from the catalog repository. The six call sites above construct
  `DriveScheduler(num_drives=..., store=context.lease_store, job_id=job.id)`.
  The CLI paths that have no catalog job yet must create one first (they
  already have `context.catalog`).
- Delete nothing else; `JobQueue` (in-memory, `bootstrap.py:607`) is not in
  scope — note it in the PR as follow-up.

### 5. Recovery on startup (item 8, recovery half only)

New `openblade/jobs/recovery.py::recover_after_restart(catalog, library) -> RecoveryReport`,
called from bootstrap after `init_db` and before the API/CLI serves anything:

1. **Only jobs whose lease has EXPIRED** (no heartbeat within the TTL) →
   `failed_recoverable`. The catalog is shared by the API and the CLI, so a
   `running` row is not evidence that a job is dead — it may be running in
   another process. A running job with no lease at all is left alone.
   (Adversarial review 2026-10-09 caught the original "fail every running
   job" wording as a cross-process regression; it never shipped.)
2. Every live lease whose job is not `running` → released (`released_at = now`).
3. For each lease released in step 2, compare expected (`barcode` in
   `physical`) with `library.inventory()`; log one line per mismatch at WARNING
   and include it in the report. **Never** move media here — report only.
4. Report is logged at INFO and returned; expose it read-only as
   `GET /jobs/recovery` (native surface, bearer-gated like the other native
   routes; 404 under `OPENBLADE_SCALAR_API_ONLY`).

Resuming a job from staged shards is **deferred** (needs the STAGING /
VERIFYING instance states from item 4) — say so in the PR, do not stub it.

## Tests (each must fail with the guard removed — mutation-check the first two)

- `tests/unit/test_drive_leases.py`: two schedulers over the same catalog
  store cannot both acquire drive 0; second waits then `DriveBusyError`.
  Fencing tokens strictly increase; a released lease's token is never reissued.
- Lease fencing (landed in `tests/integration/test_sharded_archive_atomicity.py`
  to reuse its helpers): release a lease behind a running sharded archive →
  `StaleLeaseError`, no `mark_instance_archived`, and the drive is left
  untouched (no unmount, no unload).
- `tests/unit/test_recovery.py`: seed a `running` job + live lease, restart →
  job `failed_recoverable`, lease released, mismatch reported when the
  simulator's drive is empty.
- Existing `tests/unit/test_scheduler*.py` / `test_sharded_archive_atomicity.py`
  stay green with `InMemoryLeaseStore`.
- `tests/integration/test_api.py` (or the nearest native route suite): two
  overlapping archive requests against one catalog serialise on the lease, and
  `GET /jobs/recovery` is bearer-gated and 404 in scalar-api-only mode.

## Deviations recorded at implementation time

- `DriveScheduler(store=...)` stays optional (defaults to `InMemoryLeaseStore`)
  because ~40 existing tests construct it bare; every production call site
  passes the catalog store.
- `archive.py` / `restore.py` (`run_archive_job` / `run_restore_job`) take no
  scheduler and hold no lease today, so there is nothing to fence; giving them
  leases is a follow-up. `tree_restore.py` only forwards the scheduler to the
  sharded restore, which is fenced.
- The tree-restore route builds its scheduler in the worker thread over a
  `CatalogLeaseStore(worker_catalog)` rather than `context.lease_store`: the
  context's session is not thread-safe. Same table, same exclusion.
- Explicit heartbeats run once per write batch; the scheduler's background
  heartbeat thread covers the gaps.

## Follow-up landed 2026-10-09 (phase 2: staged commits, journal, recovery report)

Item 4's states and the job journal — listed under Non-goals above for phase 1 —
landed as a foundation plus three parallel workstreams. The foundation (states,
`journal`/`job_journal`, staged-instance repository methods) and the recovery report
are on this branch; the sharded-archive wiring of those states and journal events and
the non-sharded leases come from the sibling `fleet/jobs-sharded` and
`fleet/jobs-leases` branches and are only true once those merge:

- **States.** `FileInstanceState.STAGING` / `VERIFYING` mark a sharded-archive
  instance that is written but not committed. Instances are created STAGING
  before their first write (`create_staged_instance`), moved to VERIFYING after
  every lane wrote and read back its shard, and moved to ARCHIVED in ONE
  all-or-nothing `mark_instances_archived` call only after the manifest/commit
  marker (`finalize_tape_generation`, marker written last), clean unmount and
  inventory reconcile. Staged instances are never listed as archived or
  restorable.
- **Journal.** `job_journal` rows (`CatalogRepository.journal` /
  `job_journal`) record lease_acquired/released, shard_staged, verify_started,
  verify_finished, committed, failed, fenced_out, physical_state_unknown (a
  cleanup step that raised; the original lane error is kept), and `recovered`
  (written by startup recovery for each job it fails).
- **Non-sharded leases.** `run_archive_job` / `run_restore_job` take a
  `DriveScheduler` and acquire, verify and release their drive through it like
  the sharded path, so non-sharded and sharded jobs exclude each other across
  processes; a stale lease aborts without touching hardware.
- **Recovery report fields.** `RecoveryReport` / `GET /jobs/recovery` gain
  `staged_instances` — `{job_id: [{instance_id, barcode, tape_path,
  shard_index, state}]}` for every `failed_recoverable` job, i.e. what to
  reconcile on tape before retry — and `stale_pending_job_ids`: `pending` jobs
  created more than one `DEFAULT_LEASE_TTL` ago. Jobs mark themselves `running`
  before they acquire a lease, so in practice this is an age signal: a job
  legitimately queued longer than the TTL is reported too. Both are report-only:
  no state change, no media movement. Resuming from staged shards remains
  deferred.

## Non-goals

Item 4's STAGING/VERIFYING states, job resume, replacing `JobQueue`, any
`/aml/*` or `/iblade/*` route, the emulator contract, Postgres.

## Definition of done

`make lint` clean, `.venv/bin/mypy` strict clean (CI blocks on both),
`tests/unit tests/integration tests/safety` green, frontend untouched, reviewer
subagent pass, one commit per logical change (models+repo, scheduler, fencing
at call sites, recovery+route, docs).

## JobQueue follow-up landed

The `JobQueue` deferral above (and the "replacing `JobQueue`" non-goal) is now
done as a persistent façade, not a replacement:
`JobQueue(catalog, lease_store, *, ttl=DEFAULT_LEASE_TTL)` keeps jobs only in the
catalog `jobs` table (`run_job` persists RUNNING before the work and
COMPLETED/FAILED after, error text still via `safe_job_error`), and drive/changer
ownership is a lease in the shared lease table, so it holds across processes.
`LeaseStore.acquire` allocates the lowest free ids and cannot target one, so a
claim requests every free id up to the target in one `BEGIN IMMEDIATE`
transaction, keeps the target, releases the probe leases, and retries briefly
under contention. Changer ownership is a lease on the reserved pseudo-drive
`CHANGER_DRIVE_ID = 64` (negative ids are unreachable through `range(num_drives)`;
a large sentinel would insert one probe row per lower id on every claim).
Known costs: each claim of id *k* writes up to *k* released probe rows; claims
are not heartbeated, so they lapse after the TTL. A repository method that
leases one specific drive id would remove both the probes and the retry loop.
Covered by `tests/unit/test_job_queue_persistent.py` (two sessions, one file).
