---
title: Assistant endpoint failing
document_type: runbook
component: assistant
services: [api]
failure_ids: [ASST-001]
severity: low
automation_level: diagnostic_only
risk_level: low
status: proposed
last_verified: 2026-07-19
verified_against: [docs/wiki/operations/failure-taxonomy.yaml]
owners: [platform]
tags: [runbook]
---

# RB-ASST-001 — Assistant endpoint failing

## Symptoms
`POST /assistant/chat` → 502; or `GET /assistant/status` `enabled=false`/`configured=false`.

## Evidence (read-only)
1. `/assistant/status` (enabled/configured/model/endpoint). 2. Reachability of the configured LLM endpoint. 3. Whether the API key/model are set.

## Diagnosis
- `enabled=false` → not configured (expected; no incident).
- 502 → configured endpoint down, bad key, or model unavailable.

## Remediation
Diagnostic only. Fixing the endpoint/key is a **human config change** (assistant is
advisory and has **no** impact on the archive/restore path). No autonomous action.

## Verification
`/assistant/status enabled=true` and a test chat returns a reply.

## Escalation
Low priority; notify. Never a page.

## Audit
Record status, endpoint host (not the key), conclusion.
