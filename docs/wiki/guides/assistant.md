# The OpenBlade assistant

`openblade assist` is a local chat assistant that answers questions about **your**
library: what is in which slot, which tape a file lives on, why a job failed, what a
volume group is for, and how to do a thing safely.

It runs against [Ollama](https://ollama.com), so the conversation and your catalog
never leave your machine unless you deliberately point it at a cloud endpoint.

## The safety line

**The assistant cannot run anything.** It reads state, explains, and proposes the
exact command for *you* to run. It never loads, unloads, moves, formats, erases,
archives or restores, and it never shells out.

That is not a promise made in a prompt — it is how the code is built:

| Guard | Where | What it stops |
|---|---|---|
| Tool allowlist | `openblade/assistant/tools.py` (`READ_ONLY_TOOL_NAMES`) | A tool registered without being added to the allowlist raises at startup. New tools fail **closed**. |
| Read-only proxies | `openblade/assistant/readonly.py` | Tools see a proxy over the catalog and library whose attribute allowlist contains only read methods. `catalog.create_volume_group` and `library.load` are not reachable — they raise, they do not return a callable. |
| No write path in the loop | `openblade/assistant/session.py` | No subprocess import, no database session, no `commit()`. The loop can only call handlers the registry accepted. |

All three are covered by `tests/safety/test_assistant_read_only.py`, and both
allowlist guards are mutation-checked: disable either one and those tests fail.

It will also **refuse to help you bypass a safety gate**. Ask how to skip the format
safety token and it will explain what that gate protects against and show you the
supported path instead. If you want to exercise a destructive workflow without risk,
the default simulator backend (`OPENBLADE_BACKEND=mock`) already runs the whole
thing against fake hardware.

## Setup

### Local Ollama (recommended)

```bash
# once
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2

# every session (or put it in your shell profile)
export OPENBLADE_OLLAMA_URL=http://localhost:11434
export OPENBLADE_OLLAMA_MODEL=llama3.2     # optional; this is the default
```

Pick a model that supports **tool calling** — the assistant is useless without it.
`llama3.2`, `qwen3`, `mistral-nemo` and `firefunction-v2` all work. A ~3B model is
enough for lookups; an 8B+ model writes noticeably better explanations.

### Ollama cloud

```bash
export OPENBLADE_OLLAMA_URL=https://ollama.com
export OPENBLADE_OLLAMA_API_KEY=<your key>
export OPENBLADE_OLLAMA_MODEL=gpt-oss:120b
```

The key is sent as a `Bearer` token. Note that with a cloud endpoint your questions
and the tool results — inventory, barcodes, file paths — are sent to that endpoint.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OPENBLADE_OLLAMA_URL` | *(unset)* | Ollama endpoint. **Unset = the assistant is off.** |
| `OPENBLADE_OLLAMA_MODEL` | `llama3.2` | Model name. Must support tool calling. |
| `OPENBLADE_OLLAMA_API_KEY` | *(unset)* | Sent as `Authorization: Bearer …`. Required for ollama.com. |
| `OPENBLADE_OLLAMA_TIMEOUT` | `120` | Seconds per request. Raise it if a large model is slow to load. |
| `OPENBLADE_ASSISTANT_MAX_ROUNDS` | `6` | Tool rounds per question before the loop gives up. |
| `OPENBLADE_DOCS_DIR` | repo `docs/` | Where `search_docs` looks. |

With `OPENBLADE_OLLAMA_URL` unset, `openblade assist` prints setup instructions and
exits 1. Nothing else in OpenBlade changes.

## Using it

One-shot:

```bash
openblade assist "which tape is /photos/2019/wedding.raw on?"
```

Interactive:

```bash
openblade assist
openblade> which drives are loaded right now?
openblade> /reset     # clear the conversation
openblade> /quit
```

Tool calls appear as dim single lines so you can see what was consulted:

```
· get_inventory
· catalog_search {"pattern": "wedding.raw"}
```

If an answer looks wrong, those lines tell you whether it looked anything up at all.

## What it can see

Eight read-only tools, and nothing else:

| Tool | Answers |
|---|---|
| `get_inventory` | Every slot (barcode, occupancy), every drive (loaded barcode, drive state, LTFS mount state), changer state. |
| `list_volume_groups` | All pools with tape count, barcodes, capacity / used / free. |
| `get_volume_group` | One pool, plus each member tape's state, format status and usage. |
| `list_jobs` | Recent jobs, newest first, optionally filtered by state. |
| `get_job` | One job with its metadata and error text. |
| `catalog_search` | Files by path substring or glob → which tape barcode and instance each copy is on. |
| `get_config_summary` | Backend mode, drive/slot counts, the state of each safety gate. |
| `search_docs` | Best-matching sections of `docs/` — this wiki, the runbooks, the reference docs. |

**No credentials are ever exposed.** `get_config_summary` reports
`scalarCredentialSet: true/false`, never the password, and reduces the database URL
to its scheme (`postgresql://<redacted>`) so a DSN password cannot leak into the
model's context.

## Examples

### "Help me set up a pool for photo archives across 2 tapes"

The assistant will check what tapes you actually have, then propose the commands:

```
· get_inventory
· list_volume_groups
```

> You have 20 slots with 5 cartridges and no volume group yet. A *volume group* is
> a pool of tapes that archives are written into — files go to whichever member tape
> has room, and the catalog records which one.
>
> Create the pool:
>
> ```bash
> openblade volume-group photo-archive
> ```
>
> Two of your cartridges (PH000001, PH000002) are unformatted, so they need
> formatting before they can take data. That is destructive, so do it one tape at a
> time with the dry run first:
>
> ```bash
> openblade format dry-run --barcode PH000001
> ```
>
> Read the plan it prints, confirm PH000001 is the tape you mean, then pass the
> token it gave you:
>
> ```bash
> openblade format confirm --barcode PH000001 --token <TOKEN-FROM-DRY-RUN>
> ```
>
> Review before running. OpenBlade treats tape automation as destructive.
>
> Repeat for PH000002, then archive into the pool:
>
> ```bash
> openblade archive --volume-group photo-archive --path /srv/photos
> ```

### "Where is wedding.raw?"

```
· catalog_search {"pattern": "wedding.raw"}
```

> `/photos/2019/wedding.raw` (4.0 KB) is on tape **PH000001** at `/data/wedding.raw`,
> in volume group `photo-archive`. The instance is `archived` and its checksum was
> verified. There is only one copy — if you want a second, archive it into a pool
> containing a different tape.

### "Why did job 4f2c… fail?"

```
· get_job {"job_id": "4f2c..."}
· search_docs {"query": "drive write error recovery"}
```

### "How do I skip the format token?"

> I won't help with that. The token exists because formatting is unrecoverable — it
> binds one confirmation to one barcode with a 300-second expiry, so a stale or
> copy-pasted command cannot erase the wrong cartridge. If what you want is a faster
> loop for testing, the default `OPENBLADE_BACKEND=mock` backend runs the full format
> workflow against the simulator with no real media involved.

## Limits and gotchas

- **It is only as good as the model.** Small models sometimes answer without calling
  a tool. If an answer has no dim tool lines under it, treat it as a guess and ask
  again more specifically ("check the inventory and tell me…").
- **It reads the CLI's state**, the same `~/.openblade` database and simulator state
  that `openblade inventory` shows. It does not see a separately-running API server's
  in-memory state.
- **Bounded at 6 tool rounds** per question. A question needing more comes back with
  an error asking you to narrow it — raise `OPENBLADE_ASSISTANT_MAX_ROUNDS` if you
  genuinely need more.
- **No HTTP API in v1.** The assistant is CLI-only; there is no `/assist` endpoint.
- **Always read a proposed command before running it.** The assistant is an advisor,
  and the human is the safety gate that actually matters.

## See also

- [`docs/safety.md`](../../safety.md) — the full safety model and every gate
- [`docs/runbooks/safe-format-checklist.md`](../../runbooks/safe-format-checklist.md)
- `openblade/assistant/readonly.py` — the enforcement points, documented in place
