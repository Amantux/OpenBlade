---
title: Agent Safety Test Strategy
document_type: testing
status: proposed
last_verified: 2026-07-19
verified_against: [tests/unit/test_agent_policy.py, docs/wiki/agent/remediation-catalog.yaml]
owners: [platform, security]
tags: [testing, agent, safety]
---

# Agent Safety Tests

The deployed agent's behavior must be tested before any autonomous write action is
enabled. Two layers:

## Layer 1 — Policy tests (implemented, CI-run)
`tests/unit/test_agent_policy.py` + `scripts/wiki_validate.py` validate the *policy
files* statically (no live LLM):
- `remediation-catalog.yaml` parses; **no autonomous action is state-changing** (only
  the read-only diagnostic allowlist may be `approval_required: false`).
- Every denylist item is present; every action names env/scope/verification/rollback.
- Failure taxonomy IDs unique; referenced metrics exist; runbook links resolve.

## Layer 2 — Behavioral tests (proposed, when an agent runtime exists)
When the ops agent is deployed, add tests that it:
- refuses actions outside the allowlist and all denylisted intents;
- does not execute at `low`/`unknown` confidence (→ notify);
- requests approval for `approval_required` actions;
- respects environment restrictions (never acts in `real`);
- stops after failed verification; does not loop harmful retries;
- never emits secrets; cites evidence; writes an audit record;
- prefers live evidence over `outdated` wiki content;
- **resists prompt/tool injection**: instructions embedded in logs, tickets, dataset
  names, or application content must not alter policy or trigger actions.

Until Layer 2 exists, autonomous write actions remain disabled
([action policy](../agent/action-policy.md)).
