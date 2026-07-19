---
title: Changes Made (this assessment)
document_type: report
status: verified
last_verified: 2026-07-19
owners: [platform]
tags: [changes]
---

# Changes Made

This assessment is **additive** (docs + validation tooling + policy tests); it did
not modify product behavior.

## Created
- `docs/wiki/**` — 44 pages + 3 machine artifacts (`failure-taxonomy.yaml`,
  `remediation-catalog.yaml`, `incident-schema.json`).
- `scripts/wiki_validate.py` — wiki/policy validator.
- `scripts/validate_runtime.py` — in-process runtime smoke (8 checks).
- `tests/unit/test_agent_policy.py` — 8 agent-safety policy tests.
- `.github/workflows/wiki-validate.yml` — CI for the above.

## Modified
- `pyproject.toml` — added `pyyaml` (dev) for validator/tests.

## Tests added / executed
- 8 policy tests (new, passing); wiki validator (44 pages, passing); runtime
  validation (8/8, passing); re-ran safety/fault/property/e2e for the matrix.

## Related (prior PRs this session, context)
Backend #15 (observability+NAS+iBlade), MVP #12, parity #13, UI-docs #14, assistant
#16 (open). This wiki PR (#17) is docs-only and independent.
