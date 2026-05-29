# OpenBlade

OpenBlade is a simulator-first DIY tape archive controller inspired by iBlade-style workflows for Quantum Scalar i3 and LTFS media handling. It provides a safe default mock backend, a FastAPI control plane, a Typer CLI, a SQLite-backed catalog, and regression tests for safety-critical operations.

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
OpenBlade CI/CD is intentionally split by layer so each change runs only relevant checks:

| Layer | Primary workflow/jobs | Trigger scope |
| --- | --- | --- |
| API + backend domain | `CI`: `backend-lint`, `backend-typecheck`, `backend-tests`, `api-aml-integration` | `openblade/**/*.py`, AML integration tests, backend config |
| Simulator/emulator parity | `CI`: `i3-smoke`; `i3-emulator-compliance`; `emulator-change-gates` | simulator, AML routes, emulator contract/tools, i3 tests, compose/runtime wiring |
| Frontend/UI | `CI`: `frontend-build-test` | `frontend/**` |
| CI/CD policy layer | `CI`: `cicd-workflow-validate` | `.github/workflows/**` |

This keeps checks up to date and targeted while preserving full coverage on workflow dispatch and on emulator-specific workflows.

## Quick start
```bash
pip install -e '.[dev]'
pytest -m 'not real_hardware'
cd frontend && npm install && npm run test && npm run build
openblade inventory
uvicorn openblade.api.main:app --reload
# Flask-style WSGI deployment option (same API behavior):
gunicorn openblade.api.wsgi:application
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

## Safety defaults
- Mock backend is the default
- Real hardware requires `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true`
- Use `openblade hardware connect-i3` to validate guarded Quantum i3 discovery before attempting live operations
- Use `openblade hardware validate-ltfs --device /dev/st0 --barcode ABC123L9` to validate LTFS capabilities explicitly
- Formatting requires barcode confirmation plus a one-time safety token
- Drive unload is blocked if LTFS is mounted or dirty
