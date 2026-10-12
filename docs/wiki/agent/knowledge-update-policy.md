---
title: Agent Knowledge Update Policy
document_type: policy
status: proposed
last_verified: 2026-07-19
owners: [platform]
tags: [agent, knowledge, freshness]
---

# Agent Knowledge Update Policy

The agent may propose wiki updates from incidents but MUST NOT silently rewrite
authoritative operational knowledge.

## Controlled update workflow

1. Detect a mismatch (live evidence vs a document).
2. Preserve supporting evidence (attach to the proposal).
3. Classify the change (low-risk generated vs review-required).
4. Generate a diff; mark it `status: proposed` / `unverified`.
5. Request human review when required (below).
6. Run wiki validation + relevant tests.
7. Merge only after verification; update `last_verified`.
8. Retain change history (git).

## Agent MAY auto-update (low-risk, generated)

- last-observed metric values / counts in report pages
- incident counts and links to completed incident records
- machine-generated inventories derived from code
- test-result summaries

## Agent MUST get review before changing

Runbook remediation steps · autonomous action permissions
([`remediation-catalog.yaml`](remediation-catalog.yaml)) · security guidance ·
architecture "truth" · production commands · escalation policy · rollback procedures ·
data-handling rules · permissions · secrets guidance.

## Document status model

`verified · provisionally_verified · inferred · proposed · outdated · deprecated ·
failed_validation`. The agent reduces trust in the last three and prefers live
evidence. A page whose `last_verified` is older than its component's last code change
(detectable in CI) is flagged `provisionally_verified` pending re-verification.
