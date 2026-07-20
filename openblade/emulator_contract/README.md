# OpenBlade Emulator Boundary Contract

The Quantum i3 emulator is consumed by OpenBlade as an isolated service boundary.
OpenBlade does not build emulator code from this repository; it pulls a published
image from `ghcr.io/amantux/openblade-i3-emulator`.

## Delivery boundary

- Separate source repository: `https://github.com/Amantux/openblade-i3-emulator`
- Delivery artifact: versioned Docker image
- OpenBlade override variable: `OPENBLADE_EMULATOR_IMAGE`
- Default compose image pin: `ghcr.io/amantux/openblade-i3-emulator:0.1.0`

## Client-side contract

OpenBlade expects emulator containers to:

1. Expose `GET /health` for readiness.
2. Serve AML-compatible endpoints under `/api/aml`.
3. Accept runtime configuration variables defined in `contract.json`:
   `LIBRARY_ID`, `LIBRARY_NAME`, `EMULATOR_PROFILE`,
   `EMULATOR_SLOT_COUNT`, `EMULATOR_DRIVE_COUNT`,
   `EMULATOR_OCCUPANCY_PERCENT`, and `EMULATOR_LATENCY_PROFILE`.

## Standalone packaging + runners in this repo

For external-repository/image workflows, OpenBlade carries contract-coupled runtime assets:

- Standalone compose stack: `deploy/emulator/docker-compose.standalone.yml`
- Standalone emulator UI assets: `deploy/emulator/ui/*` (served by `emulator-ui`)
- Runtime env template: `openblade/emulator_contract/standalone-runtime.env.example`
- Runner wrapper: `scripts/emulator/run_standalone_stack.sh`
- Fleet wrapper: `scripts/emulator/run_fleet_stack.sh`
- Integration hook target: `make fleet-up` (starts OpenBlade API/web + standalone emulators)

These assets include a local emulator build path for standalone development:
`deploy/emulator/Dockerfile.local` is built by
`deploy/emulator/docker-compose.standalone.yml` to avoid any GHCR dependency.

### Runtime configuration + standalone UI flow

```bash
# Optional: copy template and tune runtime knobs/ports/targets
cp openblade/emulator_contract/standalone-runtime.env.example ./emulator.runtime.env

# Validate merged standalone compose config
EMULATOR_ENV_FILE=./emulator.runtime.env make emulator-config

# Launch/inspect locally-built standalone emulator stack
EMULATOR_ENV_FILE=./emulator.runtime.env make emulator-up
make emulator-ps
```

- Default runtime env template:
  `openblade/emulator_contract/standalone-runtime.env.example`
- Local image tag env:
  `OPENBLADE_EMULATOR_LOCAL_IMAGE` (default `openblade-i3-emulator-local:dev`)
- Contracted runtime knobs:
  `EMULATOR_PROFILE`, `EMULATOR_SLOT_COUNT`, `EMULATOR_DRIVE_COUNT`,
  `EMULATOR_OCCUPANCY_PERCENT`, `EMULATOR_LATENCY_PROFILE`, and
  `OPENBLADE_SCALAR_API_ONLY` (defaults to `true` in standalone compose).

The **in-repo** app (`openblade.api.main:app`, backend=mock) now honors the shape
knobs too, not just the separate published image: `EMULATOR_PROFILE` (a named profile
such as `scalar-i3-50-3`, or the parsed `scalar-i3-<slots>-<drives>` form),
`EMULATOR_SLOT_COUNT`, `EMULATOR_DRIVE_COUNT`, and `EMULATOR_OCCUPANCY_PERCENT`
(per-field overrides). The shape is built by `openblade/simulator/i3_config.py`
(`scalar_i3_active_config()`); an unset environment yields the canonical
`scalar-i3-50-3` default, and an invalid configuration (e.g. more than 6 drives — the
documented Scalar i3 maximum) fails fast at build time. So the emulated i3 behaves
like a real i3 across the range of supported configurations, and the same `tests/i3`
suite validates every shape.

Standalone UI (`deploy/emulator/ui/*`, served by `emulator-ui`):
- Bind env: `EMULATOR_UI_BIND_HOST` (default `0.0.0.0`)
- Port env: `EMULATOR_UI_PORT` (default `5174`)
- URL: `http://localhost:${EMULATOR_UI_PORT}` (defaults to `http://localhost:5174`)
- Proxy target overrides: `EMULATOR_UI_TARGET_LIBRARY1_URL`,
  `EMULATOR_UI_TARGET_LIBRARY2_URL`, `EMULATOR_UI_TARGET_LIBRARY3_URL`
  (defaults to `http://emulator-library-{1,2,3}:8010`)
- Proxy paths exposed by nginx template (`deploy/emulator/ui/nginx.conf`):
  `/library-1`, `/library-2`, `/library-3`
- UI panels (`deploy/emulator/ui/index.html`):
  - **Connection** (API base URL, proxy prefix, auth + token controls)
  - **Library summary** (library id/name/status and slot/media totals)
  - **Drives summary** (drive status + loaded cartridge table)
  - **Slot + magazine layout** (Quantum-style `1,bay,slot` coordinates with magazine alignment)
  - **Supported APIs** (embedded FastAPI Swagger/ReDoc docs plus in-page request playground)

When `OPENBLADE_SCALAR_API_ONLY=true`, only matrix-documented Quantum AML
endpoints are reachable (plus `/health`, `/docs`, `/redoc`, and `/openapi.json`).
OpenBlade-native surfaces such as `/api/*`, `/inventory/*`, `/jobs/*`, and
non-matrix AML/iblade endpoints are rejected with `404`.

Controller target URL configuration (`OPENBLADE_EMULATOR_URLS`):
- Environment variable consumed by OpenBlade for emulator endpoints (see
  `openblade/emulator_contract/contract.json`).
- For host-run OpenBlade API:
  `OPENBLADE_EMULATOR_URLS=http://localhost:8010,http://localhost:8011,http://localhost:8012`
- For containerized OpenBlade API (`make fleet-up`), defaults in `docker-compose.yml`
  already point at host-gateway URLs:
  `http://host.docker.internal:8010,http://host.docker.internal:8011,http://host.docker.internal:8012`.

## Manual parity boundary

- `contract.json` (`scope_policy`) and the manual matrix JSON (`scope`) are both
  pinned to `manual-documented-apis-only`.
- The boundary is enforced by:
  - `tests/i3/test_15_manual_matrix_contract.py`
  - `tools/emulator_spec/validate_cross_repo_contract.py`
- Practical meaning: only manual-documented AML APIs are parity-gated; anything
  outside the manual requires an explicit policy/matrix update before it becomes
  part of compatibility guarantees.

## Matrix generation and generated suites

```bash
python3 tools/emulator_spec/build_manual_matrix.py \
  --input /path/to/quantum_webservices.txt \
  --output openblade/emulator_contract/quantum_i3_rev_h_matrix.json
```

Usage in test gates:
- `tests/i3/test_15_manual_matrix_contract.py` validates matrix/contract
  invariants (including minimum case policy and scope).
- `tests/i3/test_16_manual_matrix_generated_suites.py` validates generated suite
  depth/coverage derived from matrix case templates and return-state classes.
- `tests/i3/test_17_matrix_endpoint_coverage.py` validates endpoint-level
  route coverage and generated catalog consistency.

Endpoint catalog generation:

```bash
python3 tools/emulator_spec/generate_matrix_endpoint_catalog.py
```

Generated artifacts:
- `openblade/emulator_contract/quantum_i3_endpoint_catalog.json`
- `openblade/emulator_contract/quantum_i3_endpoint_catalog.md`

## CI gate behavior

Both emulator workflows below trigger on emulator-contract-related path changes
in pull requests and pushes to `master`, and also support manual dispatch:

- `.github/workflows/emulator-change-gates.yml`
  - Validates cross-repo contract metadata.
  - Runs matrix contract + generated suite policy tests.
  - Runs AML latency integration tests and deterministic profile invariant checks.
- `.github/workflows/i3-emulator-compliance.yml`
  - Validates cross-repo contract metadata.
  - Runs sharded i3 emulator suites (`core`, `operations`, `system`) that include
    matrix contract/generated suite gates.
  - Runs the `i3-config-<profile>` matrix: boots the emulator once per supported
    configuration (`scalar-i3-25-1`, `-50-3`, `-100-3`, `-50-6`, `-50-3-lto9`,
    `-50-4-p2`) and runs the i3 suite against each, so every shape stays compliant.

## Compatibility policy

- Contract line: `0.1.x`
- Supported image tags: semantic version tags (for stable release lines) or
  immutable SHA tags.
- Any breaking boundary change must include a contract version bump and policy
  review before OpenBlade consumes it.
- OpenBlade should fail fast on startup/test bring-up when contract expectations
  are not met.
