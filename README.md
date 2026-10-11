# OpenBlade

OpenBlade is a simulator-first DIY tape archive controller inspired by iBlade-style workflows for Quantum Scalar i3 and LTFS media handling. It provides a safe default mock backend, a FastAPI control plane, a Typer CLI, a SQLite-backed catalog, and regression tests for safety-critical operations.

📖 **Operator documentation: [docs/wiki/](docs/wiki/README.md)** — guides for every user-facing function, plus a generated [CLI](docs/wiki/reference/cli.md) and [API](docs/wiki/reference/api.md) reference.

## Features
- Simulator-first backend with deterministic library, drive, changer, and LTFS behavior
- Explicit safety gates for real hardware enablement and tape formatting
- Catalog persistence for archived files, file instances, and volume groups
- CLI and API for inventory, formatting, archive, restore, and job inspection
- Property, integration, fault-injection, and end-to-end tests

## OpenBlade goals
1. **Safety-first operations**: keep destructive and hardware-sensitive workflows gated and explicit.
2. **Simulator-first reliability**: make the Quantum i3 emulator deterministic enough for archive/restore/inventory/fault regression work.
3. **Quantum compatibility**: maintain AML/API/state behavior aligned with the documented i3/i6 Web Services surface in strict scope.
4. **Operator control plane clarity**: provide a focused API/UI/CLI for fleet and library workflows without out-of-scope feature leakage.
5. **Continuous verification**: enforce compatibility and regression evidence in CI/CD before changes land on `master`.

## Layered CI/CD (targeted)
OpenBlade CI/CD is split by layer so that each change runs only the checks that matter for it. A single required check, `ci-gate`, then aggregates the results.

Backend contract suite: `make test-contract` runs `tests/contract/` against every backend pairing (simulator, in-process AML emulator, and real hardware when explicitly enabled). A new backend must pass it before it may be selected via `OPENBLADE_BACKEND`; see docs/test-plan.md "Backend contract suite".

**PR lanes (`CI` / `ci.yml`).** These lanes run on pull requests, and `detect-changes` path-filters them:

| Layer | Jobs | Trigger scope |
| --- | --- | --- |
| API + backend domain | `backend-lint` (ruff check + format), `backend-typecheck` (`mypy openblade`, strict), `backend-tests`, `api-aml-integration` | `openblade/**/*.py`, AML integration tests, backend config |
| Simulator/emulator parity | `i3-smoke`; `i3-emulator-compliance`; `emulator-change-gates` | simulator, AML routes, emulator contract/tools, i3 tests, compose/runtime wiring |
| Frontend/UI | `frontend-build-test` (React SPA), `web-flask-smoke` (Flask NAS UI) | `frontend/**`; `openblade/web_flask/**`, `Dockerfile.web`, `docker-compose.yml` |
| CI/CD policy | `cicd-workflow-validate`; `workflow-lint` | `.github/workflows/**` |

- **Blocking.** Lint, format and `backend-typecheck` all block merge. None of these jobs uses `continue-on-error`, and `ci-gate` fails on any required job whose result is not `success` or `skipped`.
- **Ownership check.** `tools/ci_ownership.py --base origin/master` takes the changed paths from `git diff --name-only origin/master...HEAD`. It maps them through the glob table in `tools/ci_ownership.toml` to these categories: `unit`, `integration`, `safety`, `i3`, `compat`, `frontend`, `docs-only` and `ci-only`. It prints its reasoning, and it fails if a changed `openblade/**` or `tests/**` path has no owning category.
- **Emulator boot.** The reusable workflow `.github/workflows/_emulator-boot-test.yml` (`workflow_call`; inputs include `timing_profile`, `pytest_args` and `python_version`) boots the emulator, waits for it and runs pytest. `emulator-change-gates.yml` and `i3-emulator-compliance.yml` call it instead of copying those steps.

**Workflow hygiene (`workflow-lint.yml`).** This workflow runs `actionlint` (with shellcheck on inline `run:` blocks), `zizmor` and `yamllint` (`.yamllint`). Every action is pinned to a full commit SHA with a `# vX.Y.Z` comment. Workflows declare `permissions: contents: read` at the top level and grant write scopes per job.

**Nightly (`nightly.yml`).** Runs on a cron schedule and through `workflow_dispatch`. It is not a required PR check, but each of its lanes fails red:

| Lane | Selection |
| --- | --- |
| `slow` | `-m slow` |
| `stress` | `tests/fault` + `-m stress` |
| `fuzz` | `tests/property` (`-m fuzz`) |
| `rebuild` | `tests/unit/test_catalog_rebuild*` (`-m rebuild`) |
| `mutation` | `tools/mutation_run.sh` (mutmut) over the safety-critical modules; ratchet against `mutation/baseline.txt`, so new surviving mutants fail the lane. Run it locally with `make mutation`. |

**i3 timing profiles.** `I3_TIMING_PROFILE` selects a profile for both the emulator and pytest. The names `instant`, `realistic` and `hardware` still work. The new profiles are `normal`, `slow-robotics`, `busy-library`, `intermittent-drive`, `session-expiry`, `rebooting` and `degraded-media`. Profile delays go through a `Clock` protocol (`RealClock`/`VirtualClock` in `tests/i3/timing.py`), so profile tests run deterministically under `VirtualClock` without real sleeps.

## Quick start
```bash
pip install -e '.[dev]'
pytest -m 'not real_hardware'
cd frontend && npm install && npm run test && npm run build
openblade inventory
uvicorn openblade.api.main:app --reload
# Flask-style WSGI deployment option (same API behavior):
gunicorn openblade.api.wsgi:application
# Flask NAS operator UI (a separate app that talks to the API over HTTP):
FLASK_SECRET_KEY=dev-only gunicorn -k gevent -w 1 -b 0.0.0.0:5175 openblade.web_flask.app:app
```

## Multi-Library Setup
- Start the API + frontend with `make up`
- Start the standalone Quantum i3 emulators with `make emulator-up`
- Start both together with `make fleet-up`
- Seed catalog records for `library-1`, `library-2`, and `library-3` with `make seed-libraries`
- Emulator ports map as `8010=library-1`, `8011=library-2`, and `8012=library-3`
- Override controller-to-emulator targets with `OPENBLADE_EMULATOR_URLS` (comma-separated URLs)
- Add a fourth or fifth library later by calling `POST /api/libraries` with a new `name` and `emulator_url`

### Standalone emulator service workflow (external image)
- Validate standalone emulator compose config with `make emulator-config`
- Start standalone emulator services (external image + deterministic i3 defaults) with `make emulator-up`
- Build full fleet assets with `make fleet-build`
- Run OpenBlade API/web against standalone emulator services with `make fleet-up`
- Override runtime values with `EMULATOR_ENV_FILE=/path/to/env make emulator-up` using `openblade/emulator_contract/standalone-runtime.env.example` as the template
- Access the standalone Quantum i3 UI at `http://localhost:5174` (or `http://localhost:${EMULATOR_UI_PORT}` if overridden)
- Override UI proxy targets with `EMULATOR_UI_TARGET_LIBRARY{1,2,3}_URL` in the standalone env file
- Override OpenBlade controller routing with `OPENBLADE_EMULATOR_URLS` when targeting the standalone emulator endpoints

## Flask NAS operator UI
`openblade/web_flask/` is a NAS-first operator console. It is a **separate WSGI
app**, not a second implementation: it owns no domain logic and reaches the
FastAPI control plane over HTTP through `web_flask.client.BackendClient`. It sits
alongside the React SPA in `frontend/` rather than replacing it — both surfaces
are named in `CLAUDE.md`, and each has its own CI job.

Run it with `docker compose --profile web-flask up` (http://localhost:5175); it is
profile-gated so the default stack is unchanged.

Surfaces, each backed by existing API endpoints:
- **Storage → Write Path** — replication/sharding/cache-drive/ingest policy
  (`/nas/policies`, `/nas/cache-drives`, `/nas/source-stream`), and folder-to-pool
  share mappings with per-folder access modes (`/nas/shares`)
- **Storage → Catalog** — dataset table with per-dataset checksum verification
  (`/nas/datasets`, `/nas/datasets/{dataset_id}/verify`)
- **Storage → Archive / Restore** — archive and restore submission (`/archive/`,
  `/restore/`)
- **Devices** — register/probe libraries and drive inventory, mount/unmount, move,
  import/export, and magazine eject/insert against `/api/libraries` and `/aml/*`
- **System** — AML users and role assignments (`/aml/users`)

### Configuration
| Variable | Purpose |
| --- | --- |
| `FLASK_SECRET_KEY` | Session signing key. **Required** when `OPENBLADE_ENV=production` (the `Dockerfile.web` default) — the app refuses to start without it rather than minting a throwaway key that would invalidate every session on restart. |
| `OPENBLADE_WEB_BACKEND_URL` | Base URL of the FastAPI control plane. |
| `OPENBLADE_API_TOKEN` | Must match the backend's value. The backend gates its native surface (`/api`, `/jobs`, `/nas`, `/catalog`, `/status`, `/system`, `/volume-groups`, `/archive`, `/restore`) on this bearer, while `/aml/*` uses the per-user session this UI obtains at login. Omit it only where the backend also leaves native auth off. |
| `OPENBLADE_SERVICE_TOKEN` | Must match the backend's value; `/aml/mount` and `/aml/unmount` require it. |
| `OPENBLADE_WEB_SECURE_COOKIES` | Forces `Secure` session cookies; defaults on under `OPENBLADE_ENV=production`. |
| `OPENBLADE_WEB_ALLOW_UNSAFE_DEVICE_TARGETS` | Development escape hatch: permits loopback, link-local (including cloud metadata) and multicast device connection URLs. By default these are refused based on the host's **resolved addresses**, so respellings like `127.1` or `2130706433` are refused too. RFC1918 LAN addresses are always allowed — that is where real libraries live. Leave unset in production. |
| `OPENBLADE_WEB_ALLOWED_PATH_ROOTS` | Comma-separated absolute roots that operator-supplied archive/restore/cache paths must sit under. Defaults to `/openblade,/var/lib/openblade,/pools,/data,/share,/shares,/datasets,/mnt,/srv`. This is a confinement boundary, not just a traversal check: the backend's `/archive` and `/restore` routes have no per-user authorization, so without it a logged-in operator could archive `/etc` or `/proc/self/environ` to tape. |
| `OPENBLADE_CONNECT_HOST` | Pins the hostname printed in the copy-paste `net use` / `mount -t cifs` / `sftp` hints. Otherwise the (character-validated) request `Host` header is used — pin this wherever that header is not trustworthy. |
| `OPENBLADE_WEB_TIMEOUT_SECONDS` | Backend HTTP timeout, default 8. |

## Safety defaults
- Mock backend is the default
- Real hardware requires `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true`
- Use `openblade hardware connect-i3` to validate guarded Quantum i3 discovery before attempting live operations
- Use `openblade hardware validate-ltfs --device /dev/nst0 --barcode ABC123L9` to validate LTFS capabilities explicitly (always the **no-rewind** `nst` node — see `docs/hardware-setup.md`)
- Formatting requires barcode confirmation plus a one-time safety token
- Drive unload is blocked if LTFS is mounted or dirty
