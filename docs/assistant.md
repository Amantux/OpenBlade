# OpenBlade AI Assistant

An in-app helper agent that connects to any **OpenAI-compatible** chat-completions
endpoint and answers operator questions about library management, archive/restore
and sharding workflows, NAS configuration, diagnostics, and security posture —
grounded in live system state.

## Safety model

The assistant is **advisory and read-only**. It:

- inspects state through read-only tools only (inventory, drives, jobs, safety posture);
- never executes state-changing operations;
- never advises bypassing OpenBlade's safety gates (real-hardware enablement,
  format barcode + one-time token, unload-while-mounted protection).

## Configuration

Set these on the API process (e.g. the `api` service in `docker-compose.yml`).
The assistant stays **disabled** until an endpoint is configured.

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPENBLADE_ASSISTANT_BASE_URL` | Base URL, e.g. `https://api.openai.com/v1`, `http://ollama:11434/v1` | `https://api.openai.com/v1` |
| `OPENBLADE_ASSISTANT_MODEL` | Model name, e.g. `gpt-4o-mini`, `llama3.1` | `gpt-4o-mini` |
| `OPENBLADE_ASSISTANT_API_KEY` | Bearer key (omit for keyless local servers) | — |
| `OPENBLADE_ASSISTANT_ENABLED` | Force on/off | on when any of the above is set |
| `OPENBLADE_ASSISTANT_TEMPERATURE` | Sampling temperature | `0.2` |
| `OPENBLADE_ASSISTANT_MAX_TOOL_ITERATIONS` | Max tool-call rounds per turn | `4` |
| `OPENBLADE_ASSISTANT_SYSTEM_PROMPT` | Override the system prompt | built-in |

The standard `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_API_KEY` are honored as
fallbacks. Verified against OpenAI, Ollama, vLLM, and LM Studio-style servers;
endpoints that don't support the `tools` field fall back to snapshot-grounded
answers automatically.

## API

- `GET /assistant/status` → `{enabled, configured, model, endpoint, tools}` (auth required)
- `POST /assistant/chat` → `{reply, tools_used, model}` from `{messages: [{role, content}]}` (auth required)

## UI

A **Assistant** page is available in the web UI (sidebar → Dashboard → Assistant).
It shows a configuration hint when disabled, and a grounded chat once configured.
