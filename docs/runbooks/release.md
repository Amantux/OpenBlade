# Release runbook

> **Status: this pipeline has not yet been exercised on a real tag.** No `v*` tag
> has been pushed since `.github/workflows/release.yml` was written. Treat the
> first run as the test: watch every job, and expect to fix something.

Trigger: pushing a `v*` tag whose version equals `[project].version` in
`pyproject.toml` (the build job refuses a mismatch).

## Stages (all keyed on the image DIGEST, never a tag)

| # | Job | What it does | Fails the release when |
|---|-----|--------------|------------------------|
| 1 | `build` | Builds the wheel (`python -m build`) and the container **once**; pushes `ghcr.io/amantux/openblade:<version>` and outputs `digest`. Wheel uploaded as an artifact. | tag/pyproject mismatch, build error |
| 2 | `scan` | Image SBOM (anchore, SPDX JSON) + wheel SBOM (pip-audit CycloneDX); `pip-audit --strict` on the wheel's resolved deps; Trivy on `image@digest` with `severity: CRITICAL,HIGH`, `exit-code: 1`. | any known vuln in deps; any HIGH/CRITICAL in the image not in `.trivyignore` |
| 3 | `sign` | `cosign sign` (keyless, GitHub OIDC) of `image@digest`; SLSA provenance via `actions/attest-build-provenance`, pushed to the registry. | signing/attestation error |
| 4 | `verify` | `cosign verify` with issuer `token.actions.githubusercontent.com` and identity regex `^https://github\.com/Amantux/openblade/\.github/workflows/release\.yml@refs/tags/v.+$`. | signature missing or signed by any other identity |
| 5 | `release` | GitHub Release with the wheel, both SBOMs, and the digest in the body. | — |
| 6 | `deploy-staging` | Environment `staging`. `scripts/deploy.py` precheck → `docker compose -f deploy/staging/docker-compose.yml up -d --wait` → postcheck, then `postcheck --read-only-appliance-checks`, then the suites (i3 CI subset with `I3_TIMING_PROFILE=instant`, `tests/e2e/test_nas_journey.py`, `tests/unit/test_catalog_rebuild*.py`, `tests/e2e/test_mock_archive_restore.py`). | any check or suite fails |
| 7 | `deploy-production` | Environment `production` (**approval gate**). Re-runs `cosign verify`, deploys the **same digest**, read-only postcheck. | any check fails |

The suites do not accept a base URL, so they run in-process on the runner from
the tagged checkout with the released wheel installed — not against the staging
container. The staging container is covered by the two postchecks.

## Approval gate

Configure in repo **Settings → Environments → production → Required reviewers**
(at least one person who did not push the tag). That setting cannot live in the
repo; without it `deploy-production` runs unattended. The job also needs a
self-hosted runner labelled `openblade-production` on the production host.

## Rollback rule

On a failed read-only postcheck, the job runs
`scripts/deploy.py rollback --to <vars.STAGING_LAST_GOOD_DIGEST | vars.PRODUCTION_LAST_GOOD_DIGEST>`.
Update that environment variable to the new digest after a release is accepted.
If it is unset, no automatic rollback happens and an operator must run it.

- Rollback redeploys **application code only** (`OPENBLADE_IMAGE=<image>@<digest>`).
  It refuses tags and has no option that touches tape state.
- **Physical tape state is never rolled back.** Cartridges moved, loaded or
  written by the bad version stay where they are. After any rollback, review
  `GET /jobs/recovery` and reconcile before resuming operations; restore the
  catalog per `docs/disaster-recovery.md` if needed.

## Credentials

No long-lived deploy secrets. GHCR uses the job's `GITHUB_TOKEN`; signing uses
GitHub OIDC (`id-token: write` exists only on the `sign` job). Deploy targets
must authenticate by OIDC too (or run on a self-hosted runner on the target
host, as production does). Do not add registry or cloud keys as repo secrets.
Top-level `permissions: contents: read`; write scopes are granted per job.
Every action is pinned to a commit SHA with a `# vX.Y.Z` comment.

## Vulnerability exceptions (`.trivyignore`)

An entry is allowed only with, on the line above it: why the finding does not
apply (or why no fix exists), who owns it, and a review-by date. No blanket
ignores. Remove the entry once a fixed base image or dependency ships.
