# OpenBlade

OpenBlade is a simulator-first DIY tape archive controller inspired by iBlade-style workflows for Quantum Scalar i3 and LTFS media handling. It provides a safe default mock backend, a FastAPI control plane, a Typer CLI, a SQLite-backed catalog, and regression tests for safety-critical operations.

## Features
- Simulator-first backend with deterministic library, drive, changer, and LTFS behavior
- Explicit safety gates for real hardware enablement and tape formatting
- Catalog persistence for archived files, file instances, and volume groups
- CLI and API for inventory, formatting, archive, restore, and job inspection
- Property, integration, fault-injection, and end-to-end tests

## Quick start
```bash
pip install -e '.[dev]'
pytest -m 'not real_hardware'
openblade inventory
uvicorn openblade.api.main:app --reload
```

## Multi-Library Setup
- Start the API, frontend, and three emulator-backed library instances with `make up`
- Seed catalog records for `library-1`, `library-2`, and `library-3` with `make seed-libraries`
- Emulator ports map as `8010=library-1`, `8011=library-2`, and `8012=library-3`
- Add a fourth or fifth library later by calling `POST /api/libraries` with a new `name` and `emulator_url`

## Safety defaults
- Mock backend is the default
- Real hardware requires `OPENBLADE_BACKEND=real` and `OPENBLADE_REAL_HARDWARE_ENABLED=true`
- Formatting requires barcode confirmation plus a one-time safety token
- Drive unload is blocked if LTFS is mounted or dirty
