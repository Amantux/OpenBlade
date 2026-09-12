# `POST /assist` — the assistant over HTTP

The [assistant](assistant.md) has always been a CLI (`openblade assist`). `POST
/assist` exposes the same session loop to the API so a UI, a script, or another
service can ask it questions.

**It is read-only. Always.** Not "read-only by default", and not "read-only unless
you configure otherwise" — there is no configuration that makes it write.

---

## Why the HTTP surface has no tier 1

The CLI has two tiers ([assistant.md](assistant.md#the-two-tiers)). Tier 1 —
`create_volume_group`, `add_tapes_to_volume_group` — runs only after the assistant
shows you exactly what it would do and you answer `y`. That prompt is the whole
safety argument, and it needs a human at a terminal.

An HTTP request has nobody to ask. So the route builds its session with no
confirmation callback, and in `openblade/assistant/__init__.py` that single fact
decides everything:

```python
    setup_registry=build_setup_registry() if confirm is not None else None,
    setup=setup_facade(app_context.catalog) if confirm is not None else None,
    confirm=confirm,
```

> No callback, no facade, no setup registry: the write path is **absent**, not
> merely unused.

The mutating tools are never constructed, their schemas are never sent to the
model, and a model that asks for one by name gets "no such tool" back from the
read-only registry. The route then re-checks that outcome (`_require_read_only`)
before using the session, so if that factory ever changes, `/assist` fails loudly
instead of quietly gaining the ability to write.

The same reasoning covers the media tiers: anything that loads, unloads, formats,
archives or erases is REPL-only, because confirmation is interactive.

---

## Request

```http
POST /assist
Content-Type: application/json

{
  "messages": [
    {"role": "user",      "content": "how many tapes are in photo-archive?"},
    {"role": "assistant", "content": "Two: PH000001 and PH000002."},
    {"role": "user",      "content": "is either of them full?"}
  ]
}
```

- Only `user` and `assistant` roles are accepted. A `system` or `tool` message is a
  `422` — you cannot replace the assistant's own system prompt, and you cannot forge
  a tool result claiming something was executed.
- The **last** message must be `user`; that is the question being answered.
- The endpoint is stateless: the conversation travels with every request. Caps are
  40 messages and 8000 characters each.

## Response

```json
{
  "reply": "PH000001 is at 25% of 12 TB; PH000002 is at 25% as well.",
  "toolCalls": ["get_inventory", "list_volume_groups"]
}
```

`toolCalls` is **names only** — a trace of what the assistant consulted, not the
arguments it invented or the rows it read. The answer is `reply`.

## Status codes

| Code | When | What to do |
|---|---|---|
| `200` | Answered | — |
| `422` | Bad conversation shape (empty, wrong role, too long) | Fix the payload; the detail says which rule |
| `429` | Rate limited | Wait; the detail states the limit |
| `502` | The model endpoint failed, or kept calling tools past the round limit | Check Ollama; the detail is curated, never the raw upstream text |
| `503` | `OPENBLADE_OLLAMA_URL` is not set | Configure the assistant (below) |
| `404` | `OPENBLADE_SCALAR_API_ONLY=true` | Expected — a matrix-scoped i3 emulator has no assistant |

A `503` returns the same setup instructions the CLI prints:

```
The OpenBlade assistant is not configured.

Set OPENBLADE_OLLAMA_URL to an Ollama endpoint to enable it:
  local   export OPENBLADE_OLLAMA_URL=http://localhost:11434
  cloud   export OPENBLADE_OLLAMA_URL=https://ollama.com
          export OPENBLADE_OLLAMA_API_KEY=<your key>
```

---

## Configuration

The route reads the same environment as the CLI (`OPENBLADE_OLLAMA_URL`,
`OPENBLADE_OLLAMA_MODEL`, `OPENBLADE_OLLAMA_API_KEY`, `OPENBLADE_OLLAMA_TIMEOUT`,
`OPENBLADE_ASSISTANT_MAX_ROUNDS`) plus four of its own:

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_ASSIST_RATE_BURST` | `5` | Requests allowed back-to-back per client |
| `OPENBLADE_ASSIST_RATE_WINDOW_SECONDS` | `60` | Window those refill over |
| `OPENBLADE_ASSIST_MAX_CONCURRENCY` | `2` | Assistant turns running at once, process-wide |
| `OPENBLADE_ASSIST_QUEUE_TIMEOUT_SECONDS` | `30` | How long a request waits for a slot before `503` |

### Why concurrency is capped so low

A turn is a long blocking call — up to `max_rounds × timeout`, 12 minutes on the
defaults. The same process serves the Quantum AML emulator surface, and starving
that to answer a chat question is the wrong trade every time. So `/assist` runs
turns on its own small thread budget rather than the pool shared with the rest of
the app, and tells you it is busy instead of queueing indefinitely.

Raise `OPENBLADE_ASSIST_MAX_CONCURRENCY` only if the instance is not also acting
as a library emulator — and lower `OPENBLADE_OLLAMA_TIMEOUT` before you do.

### The rate limit, honestly described

A token bucket, per client address, **in one API worker's memory**. It exists
because each request costs an upstream model call, not as access control. Two
consequences worth knowing before you rely on it:

- With several workers the effective limit is per worker, not per process group.
- `X-Forwarded-For` is deliberately ignored. It is caller-controlled, so honouring
  it would let one client mint unlimited identities and defeat the limit entirely.
  Behind a reverse proxy every request therefore looks like it comes from the proxy
  and shares one bucket — raise the burst, or terminate rate limiting at the proxy.

### Authentication

`/assist` adds none of its own, because the OpenBlade-native API has none yet. It
is a plain native route, so whatever authentication lands in front of the native
surface covers it with no change here. Until then, treat `/assist` exactly like
`/inventory` or `/catalog`: do not expose it to a network you do not control. It
can read your entire catalog.

---

## Example

```bash
curl -sS localhost:8000/assist \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"why did my last restore fail?"}]}' \
  | python3 -m json.tool
```

```json
{
    "reply": "The restore of /photos/2020/landscape.raw failed: drive 0 reported a write error.",
    "toolCalls": ["list_jobs", "get_job"]
}
```

If you want it to *do* something — create a pool, add tapes — use the REPL:

```bash
openblade assist
```

That is where the `[y/N]` prompt lives, and the prompt is the point.
