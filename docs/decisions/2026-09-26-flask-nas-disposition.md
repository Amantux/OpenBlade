# Disposition: the parked Flask NAS UI and iBlade parity-coverage work

**Date:** 2026-09-26
**Status:** decided — the parked state is ENDED
**Supersedes:** [`2026-09-11-parked-flask-nas-ci-readme.md`](2026-09-11-parked-flask-nas-ci-readme.md)
**Branch:** `feat/flask-nas-disposition`

The 2026-09-11 note parked four files in `git stash` plus a set of untracked
prerequisites, because they "could be neither safely landed nor honestly
discarded". This note closes that out. Each component was evaluated against
current `master` and decided on its own.

## Summary

| Component | Disposition | Why |
| --- | --- | --- |
| `openblade/web_flask/` (+ `tests/unit/test_web_flask_app.py`) | **LANDED** | All 46 endpoints it consumes now exist on master; it needed no functional port |
| `Dockerfile.web` | **LANDED, corrected** | Shipped a dead gevent worker, a root process, and dev-mode hardening |
| `docker-compose.yml` `web` rewire | **RETIRED**, replaced | The parked version retired the React SPA as a side effect |
| `ci.yml` `web-flask-smoke` job | **LANDED, filter corrected** | Its blocker (untracked inputs) is gone; the filter was over-broad |
| `tools/emulator_spec/generate_iblade_parity_coverage.py` | **RETIRED — already on master** | Landed independently in `ce89749`; master's copy is the formatted one |
| `emulator_contract/openblade_iblade_*parity*.{json,md}` | **RETIRED — already on master** | Byte-identical to the tracked files |
| `emulator-change-gates.yml` / `i3-emulator-compliance.yml` parity steps | **RETIRED (deferred)** | Out of scope here; see "Deferred" below |
| README parity/NAS hunks | **PARTIALLY LANDED** | Two hunks were wrong and were dropped |

## What changed since the work was parked

The parked note's central blocker was that the Flask UI called NAS/AML endpoints
that existed only in the abandoned session's dirty tree. **That is no longer
true.** Extracting every path from `BackendClient` and diffing it against
`app.openapi()` (862 paths on master) gives **46 present, 0 missing**, with
matching methods. The backend surface landed independently in the intervening
commits, chiefly `bd4f3b1`.

Consequently the package needed no functional port at all:

- `pytest tests/unit/test_web_flask_app.py` — **31/31 passed unchanged**
- `mypy openblade/web_flask` (strict) — clean, 4 files
- `ruff check` — clean

The only mechanical work was `ruff format` drift, because the repo's formatter
baseline moved after the work was parked (`2026-09-11-pin-ruff-toolchain.md`).
The parity generator had drifted the *same* way, which is the tell that master's
copy is the descendant of the parked one and not a divergent reimplementation.

## Per-component rationale

### LANDED: `openblade/web_flask/`

Kept because it is not a duplicate of the React SPA. It is a separate WSGI app
that owns **no** domain logic and reaches the FastAPI control plane over HTTP
through `web_flask.client.BackendClient`, which is exactly the surface
relationship `CLAUDE.md` describes with "React frontend + Flask NAS UI". Its
content is NAS-appliance-shaped (write-path policy, cache-drive staging,
source-stream profiles, folder-to-pool share mappings, dataset checksum
verification) rather than a second rendering of the SPA's pages.

Two real auth defects were found and fixed, both by probing the running backend
rather than by reading it:

1. **The native surface was entirely broken under native auth.**
   `openblade/api/api_auth.py` gates the OpenBlade-native surface on the static
   `OPENBLADE_API_TOKEN` bearer, while `/aml/*` authenticates per-user through
   `routes_aml_auth.require_auth`. The client sent its AML *session* token as the
   only bearer, so with `OPENBLADE_API_TOKEN` set, **all 21 native endpoints it
   uses returned 401** — the whole Storage/Jobs/Reports/System surface. Verified
   against a live uvicorn instance: `/jobs/`, `/nas/policies`, `/api/libraries`
   and `/volume-groups/` all 401, while `/aml/*` worked.

   Fixed in one place. `require_auth` checks the `sessionID` cookie *before* the
   bearer, so the session token now travels as that cookie and `Authorization`
   is free for the native token. With no native token configured it falls back to
   the previous behaviour, so nothing changes for deployments that leave native
   auth off.

2. **The login lockout was bypassable.** `_client_ip()` honoured the
   caller-controlled `X-Forwarded-For`, so rotating the header minted a fresh
   rate-limit bucket per attempt and defeated `_is_login_blocked` entirely. Now
   peer-address only, matching the rule already documented on
   `openblade.api.routes_assist.client_key`.

Both fixes carry **mutation-checked** regression tests: restoring either
vulnerable line makes the new test fail (verified by doing it). Note that the
pre-existing `test_login_rate_limit_blocks_after_repeated_failures` passed
against the vulnerable code — it was vacuous for the forged-header attack, which
is precisely the failure mode the mutation rule exists to catch.

`flask` moved into the runtime dependencies: `Dockerfile.web` serves this under
gunicorn, so `pip install -e .` has to be enough to import it.

### LANDED, corrected: `Dockerfile.web`

Three defects, all found by *running* the built image. The parked CI job's only
image check was `docker compose build`, which passes on all three:

- **`-k gevent` was dead.** `gunicorn.workers.ggevent` imports `packaging`, which
  neither gunicorn nor gevent declares. The container died at start with
  `ModuleNotFoundError: No module named 'packaging'`.
- **`ENV OPENBLADE_ENV=development` was baked into the deployable artifact**,
  silently disabling both hardening checks the code offers: `_secret_key()`'s
  refusal to start without `FLASK_SECRET_KEY`, and `SESSION_COOKIE_SECURE`. Now
  `production`, with compose overriding it back for the local plain-HTTP stack.
- **gunicorn ran as root**, against the repo's own convention in `Dockerfile`.

Verified on the rebuilt image: it exits with "FLASK_SECRET_KEY is required in
production" when the key is absent; with the key it serves `/login` 200 with
`X-Frame-Options: DENY`, and every process reports `uid=999`.

### RETIRED and replaced: the `docker-compose.yml` `web` rewire

The parked hunk pointed the existing `web` service at `Dockerfile.web`, i.e. it
**replaced the React SPA with the Flask UI**. That is a bigger decision than
"land the parked tail", it contradicts `CLAUDE.md`'s two-surface description, and
it would have left `frontend-build-test` gating a service nothing deployed. The
parked note had already flagged the matching README deletion as suspect.

Replaced with an **additive** `web-flask` service behind a compose profile,
following the `emulator` precedent in the same file, so `docker compose up` is
byte-for-byte unchanged. Host port **5175**: 5173 is the React service and 5174
is the standalone emulator UI, so the parked choice of 5173 *and* an obvious
alternative of 5174 would both have clashed under `make fleet-up`.

Both services now take `OPENBLADE_API_TOKEN` and `OPENBLADE_SERVICE_TOKEN` from
one variable each so they cannot drift apart on either credential. Empty is the
documented "unset" for both, so default behaviour is unchanged. The service
token's dev default must be repeated as a literal in compose (it cannot import
Python) because `/aml/mount` and `/aml/unmount` require it — a test asserts the
two copies agree.

### LANDED, filter corrected: `web-flask-smoke`

The parked filter included `pyproject.toml`, so it fired on every dependency
bump. The `backend` filter already carries that file, so a pyproject change still
gets ruff + mypy + the whole `tests/unit` run (which imports this package);
adding it here only tacked a docker build onto Dependabot traffic.
`docker-compose.yml` is kept, because it defines the service the job builds.

Given the existing layering, the job's unique value is narrow but real:
`backend-lint` already runs ruff tree-wide and `backend-tests` already runs
`tests/unit`, but `mypy openblade` in `backend-typecheck` must stay
`continue-on-error` because of ~300 pre-existing errors in the god-file modules.
`openblade/web_flask` is strict-clean today, so this job type-checks it
**blocking** and keeps it out of that backlog. It is also the only thing in CI
that builds `Dockerfile.web`. Registered in `ci-gate`'s `needs` *and* its `check`
list — without the latter a failure would not have blocked anything.

### RETIRED (zero loss): the parity generator and artifacts

**These already landed on master** in `ce89749` ("Add iBlade Rev A parity
tracker"). The untracked copies are redundant:

- `openblade_iblade_rev_a_parity.json`, `openblade_iblade_parity_coverage.json`
  and `.md` are **byte-identical** to the tracked files.
- `generate_iblade_parity_coverage.py` differs only in line wrapping; master has
  the `ruff format`ed version, i.e. master's copy is strictly the better one.

Confirmed live: `python3 tools/emulator_spec/generate_iblade_parity_coverage.py
--allow-partial` exits 0 (`implemented=12, partial=4, missing=1`) and regenerates
the tracked artifacts with **no** diff. The parked note's recorded
`FileNotFoundError` is therefore stale, and the T3 "commits new
`emulator_contract/**` artifacts" concern no longer applies — that already
happened through the normal path.

### Deferred, not landed: the parity steps in the two emulator workflows

The generator step and artifact uploads for `emulator-change-gates.yml` and
`i3-emulator-compliance.yml` are **not** present on master — only the generator
itself landed. They are deliberately out of scope for this change, which was
scoped to touch `.github/workflows/` only for `web-flask-smoke`.

Their original blocker is gone (the generator runs green and its inputs are
tracked), so this is now an ordinary small change rather than a parked one: add
`generate_iblade_parity_coverage.py --allow-partial` plus the two artifact paths
to those workflows. Worth doing; it just is not this change. **Nothing needs to
be recovered from the stash to do it** — the hunks are reproduced in the parked
note's own diff table and are four lines of YAML.

### Partially landed: README

Taken: the `web-flask-smoke` row in the layered-CI table, and a Flask NAS UI
section — rewritten rather than copied, with a configuration table that documents
the `OPENBLADE_API_TOKEN` lockstep requirement the parked version did not know
about. Every endpoint and Flask route named in it was verified to exist.

**Not taken**, both deliberately:

- The deletion of the `cd frontend && npm install && npm run test && npm run
  build` quickstart line. The React frontend is not being retired and CI still
  gates it. (The parked note flagged this itself.)
- The reword of goal 4 from "fleet and library workflows without out-of-scope
  feature leakage" to "NAS-first operations with device-scoped deep controls".
  Landing a UI is no reason to restate the project's goals, and the reword drops
  an explicit scope-discipline clause.

## Adversarial review round (T3)

The reviewer returned **BLOCK** with four MUST-FIX items, two carrying working
repros against live sockets. All are fixed; the suite went 37 → 73 tests.

| # | Finding | Fix |
| --- | --- | --- |
| 1 | **SSRF.** The device-URL deny-list compared host *spellings*, so `127.1`, `2130706433` and `localhost.` all reached a loopback listener (proven), and any attacker-controlled DNS name could resolve into private space. Gave a logged-in user an internal port scanner and a cloud-metadata reach test. | Resolve the host and judge every returned address |
| 2 | **Redirect bypass + credential replay.** The probe followed redirects, so a host answering `302 Location: http://127.0.0.1/…` turned a blocked target into a reachable one; httpx replays the body on 307/308, so the submitted device password could be harvested. | `follow_redirects=False`; 3xx no longer counts as reachable |
| 3 | **No path confinement.** The guard blocked `..` but not *which* absolute roots were reachable, and the values become real filesystem sinks in a backend whose `/archive` and `/restore` have no per-user authz — so `/proc/self/environ`, which holds `OPENBLADE_API_TOKEN`, was archivable to tape. | `_is_allowed_storage_root` on the four genuine filesystem paths |
| 4 | **Confused deputy.** Because the client presents the instance-wide `OPENBLADE_API_TOKEN` as the native bearer, and the native config surface has no `require_auth`, **any** session could rewrite storage policy regardless of AML role. Fix (2) in the landing commit turned "broken for everyone" into "authorized for everyone", so this was a widening introduced by that change. | Role gate on the six config-write routes, resolved from `/aml/users/me`, failing closed on an unknown role |

Ten SHOULD-FIX/NIT items were also closed: dot-only identifiers (`_POOL_ID_ALLOWED`
permitted `.`, so `..` matched and collapsed a URL segment), unvalidated
`volume_group`, non-JSON upstream bodies echoed into the browser, 3xx crashing
`.json()`, an unbounded pre-auth login bucket plus a missing IP-only counter, no
logout revocation, backslash accepted in redirect targets, the reflected `Host` in
copy-paste mount commands, and bare `int()` on backend values.

**One reviewer recommendation was deliberately not taken.** It proposed adding
`is_private` to the blocked-address set. That would block `10.x`, `172.16.x` and
`192.168.x` — which is exactly where a real Scalar i3 lives — and would refuse
every legitimate device. The check blocks loopback, link-local (which covers
169.254.169.254), unspecified, multicast and reserved, and allows RFC1918. This
matches the original intent of `_DANGEROUS_DEVICE_HOSTS`, which never listed
private ranges; the defect was that it compared spellings, not that its *scope* was
wrong.

**Areas the reviewer attacked and found clean:** session/CSRF/privilege plumbing
(independently re-verified here — all 36 non-exempt endpoints 302 to login when
anonymous, and all 24 mutating routes return 400 without a CSRF token, with no GET
mutating state); template/XSS (no `|safe`, `Markup`, `render_template_string`,
inline `<script>` or `innerHTML`; `script-src 'self'` is sufficient for the six
static JS files, which write via `textContent` only).

### Mutation checks

Every new guard was mutation-checked: **13 of 13 produce a failing test when
removed.** Two first drafts were vacuous and were rewritten until they failed —
including the parametrised path-confinement test, which initially asserted the
wrong recorder and passed with the guard gone. The pre-existing
`test_login_rate_limit_blocks_after_repeated_failures` likewise passed against the
forged-header bypass, which is the failure mode this rule exists to catch.

## Verification

| Gate | Result |
| --- | --- |
| `pytest tests/unit/test_web_flask_app.py` | 73 passed (31 inherited + 42 added) |
| `mypy openblade/web_flask` (strict) | clean, 4 files |
| `ruff check .` | clean, tree-wide |
| `ruff format --check .` | clean, 352 files |
| `python3 tools/gen_wiki_reference.py --check` | up to date |
| Workflow YAML parse + `yamllint -c .yamllint` | pass |
| `docker compose build web-flask` | image built |
| Built image, no `FLASK_SECRET_KEY` | fails closed as designed |
| Built image, with key | `/login` 200, `XFO: DENY`, all processes `uid=999` |
| Live-backend auth probe, native auth ON | 7/7 native + AML endpoints OK (4/4 native were 401 before) |
| Mutation check, all 13 guards | each produces a failing test when removed |
| Anonymous sweep of every GET route | all 36 non-exempt endpoints 302 to `/login`; only `login`/`static` exempt |
| CSRF sweep of every mutating route | all 24 return 400 without a token; no GET mutates state |
| SSRF repro replay | all 11 proven bypasses blocked; 3/3 RFC1918 LAN devices still allowed |
| Redirect/credential-replay repro replay | both closed (502 before the probe proceeds) |

Not run: the full `pytest -m 'not real_hardware'` suite and the i3/emulator
gates. Nothing here touches `/aml/*` routes, `aml_state.py`, `emulator_contract/**`,
the safety gates, or the catalog schema; the backend is unmodified.

## Cleanup for the integrator

Everything worth keeping is now committed on this branch, and the rest is
verified-redundant. Once this branch is merged, the main checkout's parked state
can be dropped. **These commands were deliberately NOT run as part of this
change** — dropping a stash is irreversible and belongs to whoever owns that
working tree.

Run from the main checkout (`/root/OpenBlade`), **after** confirming
`git stash list` still shows the same entry at index 0:

```bash
# 1. Confirm you are dropping the right thing (message must start "PARKED 2026-09-11")
git -C /root/OpenBlade stash list | head -1

# 2. Drop the parked CI+README tail. Its landable content is in this branch;
#    the parity-workflow hunks are reproduced in the 2026-09-11 note's diff table.
git -C /root/OpenBlade stash drop stash@{0}

# 3. Remove the untracked prerequisites, now redundant. The parity files are
#    byte-identical to the tracked ones; web_flask/ and Dockerfile.web are
#    superseded by the corrected versions on this branch.
rm -rf /root/OpenBlade/openblade/web_flask
rm -f  /root/OpenBlade/Dockerfile.web
rm -f  /root/OpenBlade/tests/unit/test_web_flask_app.py
rm -f  /root/OpenBlade/tools/emulator_spec/generate_iblade_parity_coverage.py
rm -f  /root/OpenBlade/openblade/emulator_contract/openblade_iblade_parity_coverage.json
rm -f  /root/OpenBlade/openblade/emulator_contract/openblade_iblade_parity_coverage.md
rm -f  /root/OpenBlade/openblade/emulator_contract/openblade_iblade_rev_a_parity.json
```

Two cautions on step 3:

- `generate_iblade_parity_coverage.py` and the three `emulator_contract` files are
  **tracked on master**. They appear untracked in the main checkout only because
  that checkout sits at `f8a4ed1`, **36 commits behind `origin/master`** —
  `ce89749`, which added them, is in that gap. **The simplest and safest route is
  to skip the last four `rm`s entirely and let `git pull` reconcile them**, since
  the working copies are byte-identical to the tracked ones. If they are deleted
  first, restore with:
  ```bash
  git -C /root/OpenBlade checkout -- \
    tools/emulator_spec/generate_iblade_parity_coverage.py \
    openblade/emulator_contract/
  ```
- The main checkout has **~45 other modified/untracked files** from the same
  abandoned period that are outside this disposition's scope (`openblade/api/*`
  modifications, `openblade/hardware/scalar_http/`, `scripts/mvp_e2e_demo.py`,
  `tests/e2e/`, `deploy/emulator/observability/`, and stale `.pyc` files). Do not
  read the commands above as clearance to clean those — several have since landed
  on master in different form and need their own comparison.

## Noticed, not fixed

- `.github/workflows/ci.yml`: the comment above `ci-gate` says "backend-lint
  blocks", but the `check` list treats it as advisory and only echoes it
  alongside `backend-typecheck`. One of the two is wrong. Pre-existing.
- `openblade/web_flask/app.py` is 1,885 lines with a ~1,350-line `create_app`.
  Under `CLAUDE.md`'s god-file rule it is a future split candidate (Variant B —
  the routes close over `app` and shared helpers).
- `client.py` opens a fresh `httpx.Client` per call (≈60 methods), so no
  connection pooling. Fine at operator-UI request rates; worth a shared client if
  a page ever fans out.
- PyYAML is used by `tests/integration/test_operability_alerts.py` but is not a
  declared dependency — it is present only transitively. The compose assertions
  added here deliberately use plain text matching to avoid relying on it.
