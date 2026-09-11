# Deployment

Use the provided Dockerfile and docker-compose stack for local development. Mount persistent storage for the SQLite catalog and any future cache or staging directories. Keep production deployments in mock mode until hardware discovery and read-only inventory have been validated on the target host.

## Containerized hardware mode

`docker-compose.yml` deliberately has **no device passthrough**, so
`OPENBLADE_BACKEND=real` cannot see `/dev/sg*` or `/dev/nst*` from a container.
The recommended bring-up path is still **bare metal in the project venv**
(Python 3.12) — see `docs/runbooks/real-i3-bringup-plan.md`; containerizing the
hardware path is a later, deliberate step taken only after the library is proven.

When you are ready for it, `docker-compose.hardware.example.yml` is an opt-in
override that maps the changer and drive nodes into the `api` container and wires
`OPENBLADE_BACKEND` / `OPENBLADE_REAL_HARDWARE_ENABLED` /
`OPENBLADE_CHANGER_DEVICE` / `OPENBLADE_DRIVE_DEVICES`. It is never loaded
automatically:

```
cp docker-compose.hardware.example.yml docker-compose.hardware.yml   # then edit
export OPENBLADE_TAPE_GID=$(stat -c %g /dev/sg0)
docker compose -f docker-compose.yml -f docker-compose.hardware.yml config -q
docker compose -f docker-compose.yml -f docker-compose.hardware.yml up -d
```

Three things to know before using it, all documented in full at the top of that
file:

- Plain `devices:` is enough — no `privileged: true` and no `cap_add:` are
  required for the SCSI pass-through OpenBlade performs.
- The container drops to the unprivileged `openblade` user, so `group_add` must
  carry the host group that owns the device nodes; the override refuses to start
  if `OPENBLADE_TAPE_GID` is unset.
- The image ships `mtx` but **not** `sg3_utils` or LTFS, so drive-health and LTFS
  operations need an extended image; only read-only changer inventory works as
  shipped.

Drive devices are always the no-rewind `/dev/nstN` nodes — see
`docs/hardware-setup.md` for why.

## Validated deployment pipeline

Deploys are **gated**, not fire-and-forget: the configuration is validated before
anything goes live, and the runtime topology is verified after. A deploy that
fails either check is **not promoted** — the same discipline as the CI operability
gate, applied at deploy time.

`scripts/deploy.py` runs three stages (core in `openblade/deploy.py`,
`run_deploy_pipeline`), stopping at the first failure:

1. **Precheck** — `validate_config(env)`. Any blocking finding (unsafe default
   admin/service password, default service token, missing secrets/DB url in
   production) → **refuse to deploy; the deploy command never runs.**
2. **Deploy** — runs the operator-supplied command (list form, never
   `shell=True`). Non-zero exit → not promoted; postcheck skipped.
3. **Postcheck** — `verify_topology`: required endpoints respond and the runtime
   is wired. In-process by default; against a live deployment with `--base-url`.

Exit 0 only when the deploy is **promoted** (all three pass).

### Usage

```
# Full deploy, verifying the live result:
OPENBLADE_ENV=production \
OPENBLADE_ADMIN_PASSWORD=... OPENBLADE_SERVICE_PASSWORD=... \
OPENBLADE_SERVICE_TOKEN=... OPENBLADE_DB_URL=sqlite:////data/openblade.db \
  python3 scripts/deploy.py \
    --deploy-cmd "docker compose up -d" \
    --base-url https://openblade.internal

# Re-run pre/post checks only (no deploy), e.g. as a post-deploy smoke:
python3 scripts/deploy.py --skip-deploy

# Machine-readable:
python3 scripts/deploy.py --skip-deploy --json
```

### In CI

`operability.yml` runs the pipeline two ways: it promotes a valid dev config
(precheck + in-process postcheck), and a negative control asserts an unsafe
production config is **not** promoted — so a regression that lets a bad config
deploy fails the build. No production data or credentials are used.

### Notes / follow-ups

- `--base-url` postcheck probes endpoint reachability only (it cannot introspect
  the remote process's in-process wiring); the in-process postcheck additionally
  asserts the AppContext is fully wired.
- Rollback orchestration and blue/green promotion are out of scope here; pair this
  gate with your container platform's rollout. On a failed postcheck, do not
  promote — roll back to the last verified image and restore the catalog per
  docs/disaster-recovery.md if needed.
