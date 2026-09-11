# The OpenBlade assistant

`openblade assist` is a local chat assistant that answers questions about **your**
library: what is in which slot, which tape a file lives on, why a job failed, what a
volume group is for, and how to do a thing safely. In the interactive REPL it can
also *do* the safe part of setting up — creating a pool and putting tapes in it —
after asking you, each time.

It runs against [Ollama](https://ollama.com), so the conversation and your catalog
never leave your machine unless you deliberately point it at a cloud endpoint.

## The two tiers

| | Tier 1 — it can do this | Tier 2 — everything else |
|---|---|---|
| **What** | `create_volume_group`, `add_tapes_to_volume_group` | format, load, unload, move, eject, archive, restore, delete — and anything not in the left column |
| **How** | It shows you exactly what it would do and asks `[y/N]`. Only `y` runs it. | It proposes the exact command; **you** run it. |
| **Where** | Interactive REPL only | Everywhere |
| **Why it's allowed** | Catalog-only, reversible by hand, touches no media and no hardware | Moves media, destroys data, or cannot be undone |

Tier 1 exists because pool setup is the one part of getting started that is pure
bookkeeping — and it is the part people get stuck on. Nothing else moved: the
two-phase format flow, the real-hardware flags and the mount-state gate are
untouched, and the assistant still refuses to help you around any of them.

### What tier 1 will not do, even if you say yes

**Confirmation is not a licence to guess.** If the action cannot name exactly what
it would touch, it is refused and nothing changes:

- a barcode that isn't in this library → refused, with the barcodes you might have
  meant (prefix matches first, then tapes currently in no pool);
- a tape that is already in another pool → refused, naming that pool. Moving a tape
  between pools is a decision, so it is yours to make;
- a volume-group name that already exists → refused (the REST API answers `409` for
  the same request);
- a name with a newline in it, a non-alphanumeric "barcode", thirty tapes in one
  action → refused.

The validation runs before you are asked *and again* inside the write path, so a
`y` given a minute ago cannot act on facts that have since changed.

## The safety line

**The assistant cannot run anything except the two tier-1 actions, and never
without your yes.** That is not a promise made in a prompt — it is how the code is
built:

| Guard | Where | What it stops |
|---|---|---|
| Read-only tool allowlist | `openblade/assistant/tools.py` (`READ_ONLY_TOOL_NAMES`) | A read tool registered without being added to the allowlist raises at startup. New tools fail **closed**. |
| Read-only proxies | `openblade/assistant/readonly.py` | Read tools see a proxy over the catalog and library whose attribute allowlist contains only read methods. `catalog.create_volume_group` and the library's media moves are not reachable — they raise, they do not return a callable. The proxy keeps no instance state, so `__class__`, `__init__`, `__dict__` and `__reduce__` are refused as well: there is no route back to the live object. |
| Setup allowlist | `openblade/assistant/setup_tools.py` (`SETUP_TOOL_NAMES`) | Exactly two names may ever be tier-1 tools. Anything else raises at registry-build time. |
| Destructive-verb denylist | `openblade/assistant/setup_tools.py` (`DESTRUCTIVE_VERBS`) | A tool name containing any verb of data loss (`format`, `delete`, `erase`, `wipe`, `purge`, `destroy`, `drop`, `truncate`, `clear`, `prune`, `reset`), media movement (`load`, `unload`, `move`, `eject`, `import`, `export`, `mount`), bulk data (`archive`, `restore`, `write`) or trust (`revoke`, `deactivate`, `grant`, `token`, `rename`) is refused **even if someone also adds it to the allowlist**. Widening tier 1 to a destructive action takes two deliberate edits and a test change, not one. |
| Narrow write facade | `openblade/assistant/setup_facade.py` | Tier-1 tools do not get the catalog. They get a facade exposing five named operations; every other attribute — repository methods, `__init__`, `__reduce__`, `_target` — raises. Even the callables it hands back are sealed, because a plain closure or bound method would leak the repository through `__closure__` / `__self__`. The library backend is not behind it at all. |
| Confirmation gate | `openblade/assistant/session.py` | A tier-1 call never runs on arrival. It becomes a `PendingAction` with a preview, and only an explicit `y` executes it. No confirmation callback (one-shot mode) ⇒ the setup tools are not even offered to the model. |
| One write path | `tests/safety/test_assistant_read_only.py` | An AST scan over the whole package: `setup_facade.py` is the only file allowed to name a catalog write method. |

All of it is covered by `tests/safety/test_assistant_read_only.py`, and every
structural guard is mutation-checked — remove the guard and a named test fails
(verified, not assumed). The denylist check widens the allowlist on purpose first,
so only the denylist can be what fails it. The source scans walk the whole package
(`rglob`) and assert the exact file list, so a new module cannot quietly fall
outside the guard.

Every executed or declined tier-1 action writes one structured line to the
`openblade.assistant.setup` logger (`actor=assistant tool=… outcome=… args=…
result=…`), JSON-encoded so a crafted name cannot forge a second line.

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

Interactive (the only mode where it can execute anything):

```bash
openblade assist
openblade> which drives are loaded right now?
openblade> /reset     # clear the conversation
openblade> /quit
```

One-shot mode has nobody to ask, so it is not offered the tier-1 tools at all and
its system prompt says setup execution needs the REPL. You cannot get an
unconfirmed write by piping a question into it.

Tool calls appear as dim single lines so you can see what was consulted:

```
· get_inventory
· catalog_search {"pattern": "wedding.raw"}
```

If an answer looks wrong, those lines tell you whether it looked anything up at all.
A tier-1 action prints a yellow proposal and a prompt, and a green line after it
ran:

```
Proposed action: Create volume group 'photos' (your first pool).
Run it? [y/N] y
✓ create_volume_group applied
```

## What it can see

Eight read-only tools:

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

...and, in the REPL only, two tier-1 tools it may *perform* once you confirm:

| Tool | Does | Asks first |
|---|---|---|
| `create_volume_group` | Creates an empty pool in the catalog. Creates no tapes. | Always |
| `add_tapes_to_volume_group` | Puts cartridges that already exist into an existing pool. | Always |

There is deliberately **no** tool for removing a tape from a pool: the catalog layer
has no such operation, and the assistant does not get capabilities the rest of
OpenBlade doesn't have. (If one is ever added it cannot be called
`remove_tapes_from_volume_group` — the denylist rejects any name containing `move`.)

**No credentials are ever exposed.** Secrets are reduced at the boundary, before
the tool context is built: the Scalar password becomes `scalarCredentialSet:
true/false`, and the database URL becomes its scheme (`postgresql://<redacted>`), so
a DSN password cannot reach the model's context — or a traceback, or a log line.

## Examples

### Setting up a pool, talked through (a full REPL transcript)

This is the flagship case: you say what you want, it does the bookkeeping parts
with your say-so, and hands you the destructive parts to run yourself.

```
$ openblade assist
OpenBlade assistant. It can create a volume group and add tapes to one, and it
asks you first — [y/N] — every time. Everything else it proposes; you run it.
Type your question, or /quit to leave, /reset to clear the conversation.

openblade> I want to archive my photos onto tape. Help me get set up.
· get_inventory
· list_volume_groups
```
> You have 20 slots with 5 cartridges and no volume groups yet. A *volume group* is
> a pool of tapes that archives are written into — files go to whichever member tape
> has room, and the catalog records which one. Two of your cartridges (PH000001,
> PH000002) are unformatted and in no pool, so they are the natural candidates.
>
> Shall I create a pool called `photo-archive` and put those two tapes in it?

```
openblade> yes, call it photo-archive
· create_volume_group {"name": "photo-archive"}

Proposed action: Create volume group 'photo-archive' (your first pool).
Run it? [y/N] y
✓ create_volume_group applied
```
> Done — `photo-archive` exists and is empty. Now the tapes.

```
openblade> add both of them
· add_tapes_to_volume_group {"name": "photo-archive", "barcodes": ["PH000001", "PH000002"]}

Proposed action: Add tape(s) PH000001, PH000002 to volume group 'photo-archive'.
Run it? [y/N] y
✓ add_tapes_to_volume_group applied
```
> Done — `photo-archive` now has 2 tapes: PH000001 and PH000002.
>
> Both are still unformatted, and that part I can't do for you: formatting erases a
> cartridge and is unrecoverable, so it is a two-step flow you run yourself. Dry run
> first — it prints the plan and mints a one-time token bound to that barcode:
>
> ```bash
> openblade format dry-run --barcode PH000001
> ```
>
> Read the plan, confirm PH000001 is the tape you mean, then:
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

Two things worth noticing in that transcript. The tapes it offered were ones it had
actually *looked up* — ask it to add `PH009999` and it refuses, listing the barcodes
you might have meant, rather than creating a phantom tape. And the moment the work
turned destructive it went back to proposing; there is no tool it could have called
instead.

If you answer `n`, nothing happens, it is told you declined, and it moves on —
it will not ask you about that same action a second time:

```
openblade> actually make one called photos
· create_volume_group {"name": "photos"}

Proposed action: Create volume group 'photos' (you have 1 already).
Run it? [y/N] n
```
> All right, nothing created. Did you want to rename `photo-archive` instead, or use
> a different name for a second pool?

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
- **Bounded at 6 tool rounds** per question, and 8 tool calls per round. A question
  needing more comes back with an error asking you to narrow it — raise
  `OPENBLADE_ASSISTANT_MAX_ROUNDS` if you genuinely need more.
- **`catalog_search` is bounded too.** It narrows in SQL and scans at most 500
  candidate records; a very broad pattern reports `scanTruncated` rather than
  loading a large archive's whole catalog into memory.
- **No HTTP API in v1.** The assistant is CLI-only; there is no `/assist` endpoint.
- **Tier 1 is REPL-only**, by construction: no confirmation callback, no write
  facade, no setup tools in the schema the model is shown.
- **Read the preview, not the prose.** The `[y/N]` line above the prompt is what
  will actually happen; the model's sentence describing it is not the contract.
- **Adding several tapes is several transactions.** The catalog commits per
  cartridge, so if the database fails part-way you get `partially_applied` naming
  exactly which barcodes landed — not a silent half-write, and not a false
  "nothing happened". Check the pool before retrying.
- **A read tool hands back live ORM rows.** The proxies stop the assistant's code
  from *naming* a write; they do not make writes unreachable in the process, since
  a SQLAlchemy row knows its session. That gap is covered by the AST scans over the
  package rather than by the proxy, and it predates the two-tier model.
- **Always read a proposed command before running it.** For everything in tier 2 the
  assistant is an advisor, and the human is the safety gate that actually matters.

## See also

- [`docs/safety.md`](../../safety.md) — the full safety model and every gate
- [`docs/runbooks/safe-format-checklist.md`](../../runbooks/safe-format-checklist.md)
- `openblade/assistant/readonly.py` — the read-only enforcement points, documented
  in place
- `openblade/assistant/setup_facade.py` — the entire write surface, in one file
