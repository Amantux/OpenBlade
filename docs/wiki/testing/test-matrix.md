---
title: Test Matrix
document_type: testing
status: verified
last_verified: 2026-07-19
verified_against: [.github/workflows/ci.yml, Makefile, pyproject.toml]
owners: [platform]
tags: [testing, matrix]
---

# Test Matrix

Executed this session. Suites marked **(worktree)** were re-run on the wiki branch
(= master); others were executed earlier this session on the noted branch and are
re-run by CI. Command: `python3 -m pytest <path> -q`.

| Suite | Files | Result | Wall | Provenance |
|---|---:|---|---|---|
| `tests/safety` | 2 | 10 passed, **1 failed** (`test_import_guard`) | 7s | worktree |
| `tests/fault` | 1 | 13 passed | 4s | worktree |
| `tests/property` | 1 | 4 passed | <1s | worktree |
| `tests/e2e` | 2 | 5 passed | 20s | worktree |
| `tests/unit/test_agent_policy.py` | 1 | 8 passed | <1s | worktree (new) |
| `tests/unit` | 44 | ~779–801 passed | ~11–13m | backend branch |
| `tests/integration` (aml/nas/library sample) | 35 | 154+ passed, 2 fixed (latency) | mins | backend branch |
| `tests/i3` | 22 | 126 passed, 14 skipped | 42s | worktree/backend |
| `tests/hardware` | 9 | 56 collected, **real-only (skipped)** | — | collection only |
| frontend (`vitest`) | 3 | 6 passed; build ok | 3s | frontend |
| runtime validation (`scripts/validate_runtime.py`) | — | 8/8 checks pass | 8s | worktree (new) |

## Known non-green (documented, not new)
- `tests/safety/test_import_guard.py::test_no_direct_hardware_access_in_codebase` —
  architectural guard (routes call backend directly). **Not** in the required
  `backend-tests` CI job (which runs `test_safety_regression.py`). Tracked in
  [known failure patterns](../incidents/known-failure-patterns.md) and the
  [findings report](../reports/findings.md) (F-3).

## CI mapping (verified — `.github/workflows/ci.yml`)
`backend-tests` = `pytest tests/unit` + `test_library_commands.py` + `test_safety_regression.py`.
`i3-smoke`, `api-aml-integration`, `frontend-build-test`, `web-flask-smoke`,
`i3-emulator-*`, `emulator-contract`, plus this PR's `wiki-validate`.
