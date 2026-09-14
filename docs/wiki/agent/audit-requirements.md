---
title: Agent Audit Requirements
document_type: policy
status: proposed
last_verified: 2026-07-19
verified_against: [openblade/api/aml_state.py, openblade/api/routes_aml_system.py]
owners: [platform, security]
tags: [agent, audit]
---

# Agent Audit Requirements

Every agent decision — including "do nothing" and "notify only" — MUST produce an
audit record.

## Record fields (required)

`ts` · `agent_id` · `environment` · `trigger` (signal/alert) · `hypothesis` ·
`confidence` · `evidence[]` (source + value, cited) · `conflicting_evidence[]` ·
`candidate_action` (id) · `policy_decision` (autonomous | approval_required | denied | notify_only) ·
`approved_by` (if any) · `inputs` · `outputs` · `outcome` (succeeded|failed|skipped|escalated) ·
`verification_result` · `rollback_performed` · `wiki_sources[]` · `telemetry_sources[]`.

## Durability requirement (`verified` gap → `proposed` fix)

OpenBlade's existing audit facilities are **in-memory only and non-durable**:
`_record_audit` / `get_aml_audit_log()` (capped 1000) and `login_activity` (capped
500) live on the `AMLState` singleton and reset on restart
(`openblade/api/aml_state.py`). Therefore the agent MUST write its audit trail to a
**durable, append-only external sink** (proposed), not rely on `aml_state`. Until
that sink exists, treat agent audit durability as an open gap
([observability gap report](../operations/observability-gap-report.md)).

## Redaction

Audit records MUST NOT contain secrets, API keys, session tokens, or full sensitive
payloads (see [secrets](../configuration/secrets.md)). Redact before persisting.
