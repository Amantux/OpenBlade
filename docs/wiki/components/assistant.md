---
title: Component — assistant
document_type: component
component: assistant
status: verified
last_verified: 2026-07-19
verified_against: [openblade/assistant/, openblade/api/routes_assistant.py]
owners: [platform]
tags: [component, assistant, ai]
---

# Component: `assistant`

In-app OpenAI-compatible helper agent (read-only). Off unless configured.

| Property | Value |
|---|---|
| Config | `OPENBLADE_ASSISTANT_BASE_URL/MODEL/API_KEY` (fallback `OPENAI_*`) |
| API | `GET /assistant/status`, `POST /assistant/chat` (auth) |
| Tools | read-only: `get_library_inventory`, `get_drives_detail`, `get_safety_posture`, `get_recent_jobs` |
| Safety | advisory only; never executes state changes or bypasses safety gates |

Failure: [ASST-001](../operations/failure-taxonomy.yaml). Runbook: [RB-ASST-001](../runbooks/assistant-endpoint-down.md).

> Note: this is a **user-facing helper**, distinct from the **deployed operations
> agent** governed by [agent/operating-model.md](../agent/operating-model.md).
