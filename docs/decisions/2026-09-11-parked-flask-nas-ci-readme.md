# Parked: CI + README tail of the Flask NAS UI and iBlade parity-coverage work

**Date:** 2026-09-11
**Status:** parked in `git stash` (not lost, not landed)
**Origin:** abandoned session of 2026-08-22, checkpoint `caca9241` (see `.claude/RESUME.md`)

## Why this note exists

Hardware bring-up needs a defined baseline, so the dirty working tree had to be
reconciled. Four of the leftover files could be neither safely landed nor
honestly discarded, so they are stashed with a recovery path recorded here.

## What the abandoned work was

Two overlapping threads, both only partly in the tree:

1. **A Flask NAS web UI** (`openblade/web_flask/`) positioned as the operator
   control plane alongside/ahead of the React frontend — with `Dockerfile.web`,
   a `web` service rewire in `docker-compose.yml`, `tests/unit/test_web_flask_app.py`,
   and NAS/AML API routes to back it.
2. **iBlade parity-coverage artifacts** — `tools/emulator_spec/generate_iblade_parity_coverage.py`
   plus `openblade_iblade_parity_coverage.{json,md}` and `openblade_iblade_rev_a_parity.json`
   under `openblade/emulator_contract/`.

The six files flagged as "leftover" were only the **documentation and CI tail**
of those threads. The substance — the `web_flask` package, `Dockerfile.web`, the
generator script, and the parity JSON inputs — is still **untracked** on this
branch, alongside ~45 other uncommitted files from the same period.

## What is parked, and why it could not be landed

| File | Change | Why parked |
|---|---|---|
| `.github/workflows/ci.yml` | new `web-flask-smoke` job (`mypy openblade/web_flask`, `pytest tests/unit/test_web_flask_app.py`, `docker compose build web`) + a `webflask` path filter | Every input is untracked. At HEAD the job fails immediately, and its filter includes `pyproject.toml` / `docker-compose.yml`, so it would fire on routine changes and sit permanently red. |
| `.github/workflows/emulator-change-gates.yml` | run the parity generator + upload coverage artifacts | Generator is untracked, **and** it hard-fails at HEAD (verified) — see below. |
| `.github/workflows/i3-emulator-compliance.yml` | same generator step in two jobs + artifact paths | Same as above. |
| `README.md` | NAS-first goal rewording, `web-flask-smoke` in the layered-CI table, a "Flask NAS control-plane additions" section, and removal of the `cd frontend && npm …` quickstart line | Documents endpoints and a UI that do not exist in tracked code; the quickstart deletion also drops the still-current React build step while the CI table still lists `frontend-build-test`. |

### Evidence the parity hunks would break CI

Against a clean `git archive HEAD` tree with only the generator copied in:

```
FileNotFoundError: .../openblade/emulator_contract/openblade_iblade_rev_a_parity.json
exit=1
```

The generator needs a parity-matrix JSON that is untracked. Landing it means
committing new `emulator_contract/**` artifacts, which `CLAUDE.md` classifies as
**T3** (plan mode + reviewer first). That is a deliberate decision, not a
tree-cleanup side effect — hence parked rather than finished.

## What was landed instead

`deploy/emulator/ui/{app.js,index.html}` were coherent and complete, so they were
committed on their own (`emulator UI: live docs-link updates …`). They are backed
by `tests/unit/test_emulator_ui_docs_surface.py`, which passes 3/3 with the change
and fails 2/3 without it.

## How to recover

```bash
git stash list                 # find the entry whose message starts "PARKED 2026-09-11"
git stash show -p stash@{N}    # review before applying
git stash apply stash@{N}      # apply (keeps the stash); use `pop` to consume it
```

Do not apply it alone. To land it, land it **together with** its prerequisites:

- for the CI job: `openblade/web_flask/`, `Dockerfile.web`, the `docker-compose.yml`
  `web` service rewire, and `tests/unit/test_web_flask_app.py`;
- for the parity steps: `tools/emulator_spec/generate_iblade_parity_coverage.py`
  and `openblade/emulator_contract/openblade_iblade_rev_a_parity.json` — via the
  T3 path, since it moves the parity contract.

Re-check the README quickstart hunk on the way in: keep the React build line
unless the React frontend is actually being retired.
