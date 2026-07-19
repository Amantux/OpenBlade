---
title: System Context
document_type: architecture
status: verified
last_verified: 2026-07-19
verified_against: [openblade/api/main.py, docker-compose.yml, openblade/config.py]
owners: [platform]
tags: [architecture, context]
---

# System Context

```mermaid
flowchart LR
  op[Operator] --> web[web / React UI]
  op --> flask[web_flask / NAS console]
  op --> cli[cli]
  web --> api[api :8000]
  flask --> api
  cli --> api
  api --> catalog[(catalog SQLite)]
  api --> backend{backend}
  backend -->|simulator| sim[Mock library+LTFS]
  backend -->|real scsi| hw[mtx + /dev/st LTFS]
  backend -->|webservices| realI3[real Quantum i3 /aml]
  api -->|fleet probe/http| emu[emulator x3 :8010-8012]
  api --> asst[assistant] --> llm[OpenAI-compatible endpoint]
```

## Actors
- **Operator** (human): archive/restore, library management, NAS config.
- **AI ops agent** (automated): read-only diagnostics + gated remediation (see [agent](../agent/operating-model.md)).
- **External systems**: an OpenAI-compatible LLM endpoint (assistant, optional); a real Quantum i3 (webservices/scsi backends, optional).

## Sensitive assets
- Tape data + LTFS filesystems (data path). Catalog metadata. Auth credentials/sessions.
  Real-hardware control (destructive). See [trust boundaries](trust-boundaries.md).

## Operational dependencies
- SQLite catalog file; the selected backend; (for fleet) reachable emulator URLs;
  (for assistant) the configured LLM endpoint.
