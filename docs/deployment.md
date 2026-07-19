# Deployment

Use the provided Dockerfile and docker-compose stack for local development. Mount persistent storage for the SQLite catalog and any future cache or staging directories. Keep production deployments in mock mode until hardware discovery and read-only inventory have been validated on the target host.

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
