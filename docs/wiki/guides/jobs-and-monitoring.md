# Jobs & monitoring

---

## Jobs are synchronous. There is no worker.

This is the most important fact on the page, and the API actively misleads you
about it.

- `openblade archive` and `openblade restore` **run the job inline** and block
  until it finishes. Their help text says "Enqueue…"; they do not enqueue.
- `POST /archive/`, `POST /archive/sharded` and `POST /restore/` run the job
  **inside the request handler**, then return `202 Accepted` with
  `"status": "pending"`. That string is a hardcoded literal.

Verified: a `202 … "pending"` response from `POST /archive/sharded` was followed
immediately by `GET /jobs/` showing that job already `completed`.

There is no `BackgroundTasks`, no thread pool, no asyncio task and no worker
process in the request path. `openblade/jobs/worker.py` exists and its `run()`
method is never called by anything.

Practical consequences:

- Set generous client and reverse-proxy timeouts. A large archive holds the HTTP
  connection open for its entire duration.
- Never treat `202 / pending` as "accepted, come back later". Poll
  `GET /jobs/{id}` for the real state.
- If the process dies mid-archive, nothing resumes. There is no resumable-job
  machinery to recover.

All archive requests are serialised by a single process-wide lock. Restores are
not.

---

## Job states

`pending` · `running` · `completed` · `failed` · `failed_recoverable` ·
`cancelled`

In practice only `pending → running → completed | failed` are ever written by the
classic engines. `failed_recoverable` is written **only** by the sharded archive
engine, when at least one file or batch errored. `cancelled` is never set by
anything.

There is no state-machine validation on the column — the update helper writes
whatever string it is given.

Job types: `archive`, `restore`, `format`, `verify`, `inventory`, `import`,
`export`.

---

## Watching jobs

```bash
openblade jobs                                    # table of all jobs
openblade jobs 4e4930fc-e230-4016-85a8-9d0a0096f391
```

```json
{
  "id": "4e4930fc-…",
  "state": "completed",
  "job_type": "restore",
  "error": null,
  "metadata": {"catalog_path": "/demo-vg/note.txt", "dest_path": "/tmp/out/note.txt"}
}
```

Over HTTP: `GET /jobs/` and `GET /jobs/{job_id}`.

Both were run against the simulator while writing this page.

### Where errors show up

| Surface | What you get |
|---|---|
| `jobs.error` | The exception text. Shown by `openblade jobs` and the API. |
| `tape_op_log` | Per-operation audit: op type, barcode, drive, slot, result JSON, error. Read via `GET /tape-ops`. |
| HTTP status | 404 (not found), 409 (cartridge offline), 503 (no scratch media / tape full), 400 (everything else, including every safety violation) |

Job history lives in SQLite and survives restart. The AML-side synthetic job and
event records that feed `/dashboard/stats` are **in-memory and lost on restart**.

### Retry

None, for classic archive and restore jobs. Re-issue the command.

NAS restore jobs have operator-triggered `retry` (from `FAILED` only), `cancel`,
`pause` and `resume`. Nothing retries automatically anywhere in the system.

---

## Health and readiness

| Endpoint | Auth | Checks |
|---|---|---|
| `GET /healthz` | none | three components — database, library, LTFS; worst status wins |
| `GET /readyz` | none | ready only if **database and library** are both OK. LTFS is excluded. |
| `GET /version` | none | version, git commit, build date |
| `GET /error-codes` | none | the known error-code catalogue |
| `GET /status/library` | **yes** | inventory, drive list, slot totals, cartridges loaded |
| `GET /status/catalog` | **yes** | row counts, last catalog-rebuild run |
| `GET /system/config-summary` | **yes** | backend, CORS origins, max upload, db path, library count |
| `GET /health` | none | static `{"status":"ok"}` — **checks nothing** |

Verified against the simulator: `/healthz`, `/readyz` and `/version` return 200
unauthenticated; `/status/catalog` and `/status/library` return **401**.

Component semantics:

- **database** — four independent probes. All pass → `ok`; all fail →
  `unhealthy`; some fail → **`degraded`**, naming the failing probes. A count
  that could not be read is reported as `-1`.
- **library** — `ok` if at least one drive is present, `degraded` if connected
  with zero drives, `unhealthy` on exception.
- **ltfs** — duck-typed. The mock backend always reports `ok`, so this component
  tells you nothing in simulator mode.

> ⚠️ `docker-compose.yml` health-checks **`/health`**, not `/healthz`. A
> container whose database is unreachable still reports healthy, and dependent
> services start anyway. If you write your own orchestration, probe `/readyz`.

---

## Metrics

**There is no `/metrics` on the native surface.** Verified: `GET /metrics`
returns 404.

Prometheus exposition lives at:

```
GET /aml/system/emulator/latency/metrics/prometheus
```

Three things to know before you point a scraper at it:

1. **It is authentication-gated.** `docs/monitoring.md` tells you to scrape it
   and does not mention credentials. Your scrape config needs them.
2. **The data is in-memory** and resets on every restart. Counters are not
   durable.
3. It lives under the Quantum emulator surface, so it disappears in modes where
   that surface is scoped out.

Series exposed include `openblade_system_uptime_seconds`,
`openblade_component_status{component=…}`, `openblade_jobs_state_total`,
`openblade_media_utilization_percent`, `openblade_media_capacity_bytes`,
`openblade_drive_state_total`, `openblade_cleaning_media_total`, and per-request
latency counters. JSON variants exist at `…/metrics` and `…/metrics/export`;
`…/metrics/reset` is admin-only.

---

## What is *not* a safety mechanism

`docs/safety.md` and `docs/architecture.md` credit the job queue with preventing
two jobs from claiming one drive, and with serialising changer moves.
`JobQueue.claim_drive()` and `claim_changer()` are **never called by any
production code**. A `JobQueue` is constructed at startup and threaded into the
services, but none of them use its ownership methods, and its state never reaches
the database.

What actually serialises work:

- the process-wide archive lock (archive requests only),
- the orchestrator's per-barcode and per-drive locks (read/verify, write/format),
- `DriveScheduler`, for sharded jobs — which is real, but is constructed **per
  request**, so it does not serialise across concurrent requests.

---

## Related

- [Archiving](archiving.md) · [Restoring](restoring.md)
- [Safety model](safety-model.md)
- [Troubleshooting](troubleshooting.md)
- `docs/monitoring.md`
