# Releasing

Version is single-sourced in `pyproject.toml`'s `[project].version`. There is
no auto-bump automation in this repo (unlike the sibling-repo playbook some
teams use) -- bumping and tagging are both manual, deliberate steps.

## Cut a release

1. **Bump the version.** Edit `version = "X.Y.Z"` in `pyproject.toml`. Nothing
   else needs to change in lockstep (there is no HACS `manifest.json` or
   similar to keep in sync in this repo).
2. **Commit and merge to `master`** through the normal PR/CI path. `make all`
   (or CI's `backend-lint`/`backend-test` jobs) must be green before tagging --
   the release workflow does not re-run the test suite.
3. **Tag and push the tag:**
   ```bash
   git tag vX.Y.Z   # e.g. v0.2.0 -- MUST match pyproject's version exactly
   git push origin vX.Y.Z
   ```
   The `v` prefix is required: `.github/workflows/release.yml` only triggers
   on `v*` tag pushes, and its first step fails the build if the tag's version
   doesn't match `pyproject.toml`'s -- a mismatched tag is rejected rather than
   silently publishing the wrong version.

## What CI does

`.github/workflows/release.yml` runs on the tag push:

1. Checks out the tagged commit.
2. Cross-checks the tag against `pyproject.toml`'s version (fails fast on a
   mismatch, before anything is built or pushed).
3. Builds the controller image from the root `Dockerfile` and pushes it to
   `ghcr.io/amantux/openblade`, tagged both `<version>` and `latest`.
4. Creates a GitHub Release from the tag with auto-generated release notes
   (commits since the previous tag).

It does **not** build a separate web image: this repo has `frontend/Dockerfile`
(consumed by `docker-compose.yml`'s `web` service for local dev) but no
top-level `Dockerfile.web`, so there is nothing to publish standalone yet. If
one is added later, extend the workflow to publish
`ghcr.io/amantux/openblade-web` alongside the controller image.

Permissions are least-privilege and job-scoped: the workflow's default is
`contents: read` (matching every other workflow in this repo); only the
`release` job grants `contents: write` (to create the Release) and
`packages: write` (to push to GHCR).

## How operators pull the image

```bash
docker pull ghcr.io/amantux/openblade:X.Y.Z   # a specific release
docker pull ghcr.io/amantux/openblade:latest  # the most recent tagged release
```

`docker-compose.yml`'s `api` service builds the image locally from `Dockerfile`
by default; point `image:` at the GHCR tag instead of `build:` to run a
published release without a local build, the same way the `emulator` service
already does for the published i3 emulator image.

## First-run caveat

This workflow has never been exercised by an actual `v*` tag push -- it is
reviewed (YAML parses, `bash -n` on every `run:` block, permissions scoped
correctly) but not yet proven end to end. If the first real tag push fails,
check in this order: the GHCR login step (repo's Actions have `packages:
write` by default, but organization-level policies can still block push --
check **Settings → Actions → General → Workflow permissions**), the
`docker/build-push-action` step's build logs for a Dockerfile error unrelated
to this workflow, and finally whether `softprops/action-gh-release` needed
`contents: write` that wasn't granted (it is, in the job's `permissions:`
block, but a branch-protection rule on tags could still interfere).
