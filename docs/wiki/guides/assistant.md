# The OpenBlade assistant

`openblade assist` is a local chat assistant that answers questions about **your**
library: what is in which slot, which tape a file lives on, why a job failed, what a
volume group is for, and how to do a thing safely. In the interactive REPL it can
also *do* things — set up a pool, load a tape, archive a directory, even format a
cartridge — after asking you, each time, with a confirmation matched to what the
action costs.

It runs against [Ollama](https://ollama.com), so the conversation and your catalog
never leave your machine unless you deliberately point it at a cloud endpoint.

## The three tiers

| | Tier 1 — setup | Tier 2 — media & robotics | Tier 3 — everything else |
|---|---|---|---|
| **What** | `create_volume_group`, `add_tapes_to_volume_group` | `load_tape`, `unload_drive`, `move_tape`, `archive_path`, `restore_path`, `format_tape` | eject, import/export, delete, configuration — and anything not to the left |
| **How** | Shows exactly what it would do and asks `[y/N]`. Only `y` runs it. | Shows the cartridge, slot, drive, drive serial and cost, then asks. Load/unload/move/archive take `y`; **format**, and a **restore that would overwrite a file**, make you TYPE the barcode or the word shown — `y` is refused. | It proposes the exact command; **you** run it. |
| **Where** | Interactive REPL only | Interactive REPL only | Everywhere |
| **Why it's allowed** | Catalog-only, reversible by hand, touches no media | Goes through the same services the REST API and CLI use — job-queue drive ownership, `RealHardwareGuard`, the mount-state gate and the one-time format token all still apply | It doesn't have a tool, and won't get one |

Tier 1 exists because pool setup is pure bookkeeping and it is the part people get
stuck on. Tier 2 exists because "load OB0003L8 into drive 1" is a sentence, not a
command line — but it is also a robot arm moving a cartridge, so the confirmation is
graded by consequence rather than being one blanket `y`.

**The assistant is a new caller, not a new path.** Every tier-2 action runs through
`TapeOperationOrchestrator` (via `execute_tape_request`), `FormatService`,
`ArchiveService` or `RestoreService` — the same objects `POST /tape-ops/execute`,
`openblade format confirm` and `openblade archive` use. Nothing about the two-phase
format flow, the real-hardware flags or the mount-state gate was softened to make
this work, and the assistant still refuses to help you around any of them.

### What tier 2 will not do, even if you confirm

Same house rule as tier 1, applied **before** you are prompted — you are never asked
to confirm an action that would then have to guess:

- a barcode that is not in this library → refused, with the barcodes you might have
  meant (prefix matches first);
- a barcode the inventory reports in two places at once → refused, naming both. It
  means the inventory disagrees with the changer, and acting on that is how media
  gets crushed;
- a target drive that already holds a cartridge, or a target slot that is already
  full → refused, naming the occupant;
- `unload` with neither a barcode nor a drive while two drives are loaded → refused
  with the candidates. Guessing here unloads the wrong tape;
- a drive whose LTFS volume is still mounted or dirty → refused, for unload **and
  for format**. Formatting runs `mkltfs` against a drive, and the orchestrator will
  load a slotted cartridge to do it, so a format is also a load — the preview says
  which drive it will use, and a mounted volume stops it;
- an archive whose files are already catalogued at those paths → refused. The
  catalog write is an upsert, so it would replace the record describing the copy
  already on tape; that is a deliberate act for `openblade archive`, not something
  to slip through a `y`;
- a restore whose destination **changed** between the preview and your answer →
  refused. Whether a restore overwrites is a fact about the filesystem sampled when
  the plan ran, and it is what chose the confirmation grade; if a file appeared
  while you were reading, the `y` you gave was for a different action;
- a destination slot that is an import/export element → refused. Moving media out of
  the library is an export, and the orchestrator rejects it too;
- a relative source path, a missing directory, an unknown volume group, an unknown
  catalog path, a tree over 500 files → refused with the reason.

Everything is validated again inside the write path, so a confirmation given a
minute ago cannot act on facts that have since changed.

### Why format still takes two phases

Calling `format_tape` does not format anything. It runs the **real** dry run —
`FormatService.dry_run`, which mints a one-time `SafetyToken` bound to that barcode
and persists it — and shows you the plan: what is on the cartridge, how much of it,
which pool, the capacity, what the format writes, and how long the token is good
for. Only after you type the barcode does it present that token back to
`FormatService.confirm`, which validates it against the persisted row and deletes
it. There is no branch in which a missing token merely skips a check: no dry run
means no token means the format fails.

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

**The assistant can run only the two tier-1 actions and the six tier-2 actions, and
never without a confirmation strong enough for what the action costs.** That is not
a promise made in a prompt — it is how the code is built:

| Guard | Where | What it stops |
|---|---|---|
| Read-only tool allowlist | `openblade/assistant/tools.py` (`READ_ONLY_TOOL_NAMES`) | A read tool registered without being added to the allowlist raises at startup. New tools fail **closed**. |
| Read-only proxies | `openblade/assistant/readonly.py` | Read tools see a proxy over the catalog and library whose attribute allowlist contains only read methods. `catalog.create_volume_group` and the library's media moves are not reachable — they raise, they do not return a callable. The proxy keeps no instance state, so `__class__`, `__init__`, `__dict__` and `__reduce__` are refused as well: there is no route back to the live object. |
| Setup allowlist | `openblade/assistant/setup_tools.py` (`SETUP_TOOL_NAMES`) | Exactly two names may ever be tier-1 tools. Anything else raises at registry-build time. |
| Destructive-verb denylist | `openblade/assistant/setup_tools.py` (`DESTRUCTIVE_VERBS`) | A tool name containing any verb of data loss (`format`, `delete`, `erase`, `wipe`, `purge`, `destroy`, `drop`, `truncate`, `clear`, `prune`, `reset`), media movement (`load`, `unload`, `move`, `eject`, `import`, `export`, `mount`), bulk data (`archive`, `restore`, `write`) or trust (`revoke`, `deactivate`, `grant`, `token`, `rename`) is refused **even if someone also adds it to the allowlist**. Widening tier 1 to a destructive action takes two deliberate edits and a test change, not one. |
| Narrow write facade | `openblade/assistant/setup_facade.py` | Tier-1 tools do not get the catalog. They get a facade exposing five named operations; every other attribute — repository methods, `__init__`, `__reduce__`, `_target` — raises. Even the callables it hands back are sealed, because a plain closure or bound method would leak the repository through `__closure__` / `__self__`. The library backend is not behind it at all. |
| Confirmation gate | `openblade/assistant/session.py` | A tier-1 call never runs on arrival. It becomes a `PendingAction` with a preview, and only an explicit `y` executes it. No confirmation callback (one-shot mode) ⇒ the setup tools are not even offered to the model. |
| One write path | `tests/safety/test_assistant_read_only.py` | An AST scan over the whole package: `setup_facade.py` is the only file allowed to name a catalog write method. |
| Media allowlist | `openblade/assistant/media_tools.py` (`MEDIA_TOOL_NAMES`) | Exactly six names may ever be tier-2 tools, and the registry additionally refuses any name already claimed by the tier-1 or read-only registries. The three registries are provably disjoint, so a tool cannot cross a tier boundary by being renamed — and every tier-2 name (`load`, `unload`, `move`, `format`, `archive`, `restore`) is permanently *unregistrable* as a tier-1 tool, because the tier-1 denylist above still rejects all of them. |
| Media facade | `openblade/assistant/media_facade.py` | Tier-2 tools do not get the library backend, the LTFS backend or the repository. They get a facade over thirteen named operations; every other attribute — `library`, `ltfs`, `catalog_repo`, `__init__`, `__reduce__`, `_target` — raises, and the callables it returns are sealed so `__closure__` cannot leak the bundle. Its own validation reads go through the tier-1 read-only proxy. |
| Confirmation grade | `openblade/assistant/media_tools.py` (`ConfirmationGrade`) | The grade is a property of the tool and its resolved plan, never of the model's arguments — a format is always `TYPED`, a restore is `TYPED` exactly when the filesystem says the destination exists. `authorize()` turns what you typed into an authorization; `perform()` **re-verifies** it against the action before calling anything. A forged authorization, one issued for a different action, or a `y` against a typed grade raises and nothing runs — so the strength of the confirmation does not depend on the REPL prompt being written correctly. |
| One media path | `tests/safety/test_assistant_read_only.py` | A second AST scan: `media_facade.py` is the only file allowed to import `openblade.nas` or name `execute_tape_request` / `TapeOpRequest`. `__init__.py` may wire the job services into the bundle and nothing else may name them. |

All of it is covered by `tests/safety/test_assistant_read_only.py`, and every
structural guard is mutation-checked — remove the guard and a named test fails
(verified, not assumed). The denylist check widens the allowlist on purpose first,
so only the denylist can be what fails it. The source scans walk the whole package
(`rglob`) and assert the exact file list, so a new module cannot quietly fall
outside the guard.

Every proposed, declined, refused, failed or executed action — both tiers — writes
one structured line to the `openblade.assistant.setup` logger (`actor=assistant tool=… outcome=… args=…
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

One-shot mode has nobody to ask, so it is offered **neither** tier's tools and its
system prompt says execution needs the REPL. You cannot get an unconfirmed write —
or an unconfirmed format — by piping a question into it.

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

A tier-2 action prints the same way, in red when it is destructive, and the prompt
demands the typed word rather than a yes:

```
Proposed action: FORMAT OB0007L8. This is irreversible and there is no undo.
  ...
  Type the barcode OB0007L8 to confirm. Anything else — including "y" — cancels.
Type OB0007L8 to confirm (anything else cancels): OB0007L8
format_tape: running now — this can take minutes.
format_tape: done — formatted
✓ format_tape applied
```

`archive_path` and `restore_path` block the REPL while they run, so they print a
line before and after — the trailing line carries the job id and the verified byte
count, because the services create the job inside the same call that runs it and
there is no id to print before it returns.

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
| `create_volume_group` | Creates an empty pool in the catalog. Creates no tapes. | Always — `[y/N]` |
| `add_tapes_to_volume_group` | Puts cartridges that already exist into an existing pool. | Always — `[y/N]` |

...and six tier-2 tools, each behind a confirmation graded by what it costs:

| Tool | Does | Confirmation |
|---|---|---|
| `load_tape` | Moves a cartridge from its slot into a drive. | `[y/N]` on a preview naming tape, slot, drive and drive serial |
| `unload_drive` | Returns a loaded cartridge to a storage slot. Refused while LTFS is mounted. | `[y/N]` |
| `move_tape` | Moves a cartridge between two storage slots. Import/export elements are not valid destinations. | `[y/N]` |
| `archive_path` | Copies a local file or directory onto the tapes of a pool and catalogues it. Additive. | `[y/N]`; reports job id, files and bytes verified against the catalog afterwards |
| `restore_path` | Copies an archived file back to a local path, checksum-verified. | `[y/N]` normally — **type `OVERWRITE`** if the destination file already exists |
| `format_tape` | Erases a cartridge with LTFS. Irreversible. | Dry run first, then **type the barcode**. A `y` is refused |

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
OpenBlade assistant. It can set up volume groups, and load, unload, move,
archive, restore and format media — and it asks you first, every time. A
format, or a restore that would overwrite a file, makes you type the barcode
or the word shown; "y" will not do it. Everything else it proposes; you run it.
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

### Moving and writing real media (a full REPL transcript)

Captured verbatim against the **mhvtl rehearsal rig** — a real SCSI changer, real
`/dev/nst*` drives, real `mkltfs`, real LTFS mounts — with
`OPENBLADE_BACKEND=real`, `OPENBLADE_REAL_HARDWARE_ENABLED=true` and
`OPENBLADE_DRIVE_SERIAL_MAP="OBLADE_D01:0,OBLADE_D02:1,OBLADE_D03:2"`. Reproduce
it with `eval "$(scripts/mhvtl/env.sh)"`; see
[`scripts/mhvtl/README.md`](../../../scripts/mhvtl/README.md). The model is a small
local one (`qwen2.5:3b`), so its prose is sometimes loose — the previews, prompts
and results are the product, and they are exact.

**1. Load a cartridge.** Non-destructive robotics, so a `y` is enough — but the
preview names the drive *serial*, which is the thing you can check against a label
on the rack:

```
openblade> load tape OB0003L8 into drive 1
· load_tape {"barcode": "OB0003L8", "drive": 1}

Proposed action: Load OB0003L8 from slot 3 into drive 1 (serial OBLADE_D02).
  Slot 3 becomes empty; the cartridge is not written to and nothing is mounted.
Run it? [y/N] y
[info     ] tape operation queued          barcode=OB0003L8 op_id=4bae7957-… op_type=load
[info     ] tape operation completed       barcode=OB0003L8 op_id=4bae7957-… op_type=load
load_tape: done — loaded
✓ load_tape applied
```

**2. Put it back.** Asked by drive, not by barcode — the assistant resolves which
cartridge that is and names it back to you before doing anything:

```
openblade> unload the tape in drive 1 back to slot 3
· unload_drive {"barcode": null, "drive": 1}

Proposed action: Unload OB0003L8 from drive 1 (serial OBLADE_D02) into slot 3 —
the lowest free slot.
  Drive 1 (serial OBLADE_D02) becomes free. Nothing on the cartridge changes;
LTFS is not mounted on it.
Run it? [y/N] y
unload_drive: done — unloaded
✓ unload_drive applied
```

**3. Format a scratch cartridge.** The destructive grade. The dry run has already
run and minted the one-time token by the time you see this; a `y` here cancels.
Note that the preview names the drive it will *load the cartridge into* — a format
is also a media move, and the orchestrator would do it whether or not you were
told:

```
openblade> format the scratch tape OB0008L8
· format_tape {"barcode": "OB0008L8"}

Proposed action: FORMAT OB0008L8. This is irreversible and there is no undo.
  Everything on the cartridge is destroyed: 80 archived file(s) in volume group
'hw-catalog-stripe', 0 B recorded as used of 12.0 GB capacity.
  WORM: not reported by this backend — check the cartridge label yourself if it
matters.
  The format writes a new LTFS label, index partition and data partition.
  A one-time safety token was issued by the dry run and expires in 299s — if it
expires while you check the cartridge, ask again and a fresh dry run runs.
  The cartridge is in slot 7 and will be loaded into drive 0 to format it, then
returned.
  Files that would be lost include:
/block_stripe/0b5ac0c2-…/catalog.bin.shard0001,
/block_stripe/1918e46e-…/block-stripe.bin.shard0001, …
  Dry run: Destructive operation
  Dry run: Inventory barcode must match confirmation
  Type the barcode OB0008L8 to confirm. Anything else — including "y" — cancels.
Type OB0008L8 to confirm (anything else cancels): OB0008L8
format_tape: running now — this can take minutes.
[info     ] tape operation queued          barcode=OB0008L8 op_id=da0bf47f-… op_type=format
[info     ] tape operation completed       barcode=OB0008L8 op_id=da0bf47f-… op_type=format
format_tape: done — formatted
✓ format_tape applied
```

Had LTFS still been mounted on that cartridge, this would have been refused before
the prompt — the same gate that stops an unload, applied to the operation that
cannot be undone.

**3b. The same prompt, answered `y`.** Nothing happens — no tool ran, the token was
not consumed, and the model is told the confirmation was not given rather than
being allowed to report success:

```
openblade> format tape OB0007L8
· format_tape {"barcode": "OB0007L8"}

Proposed action: FORMAT OB0007L8. This is irreversible and there is no undo.
  ...
  Type the barcode OB0007L8 to confirm. Anything else — including "y" — cancels.
Type OB0007L8 to confirm (anything else cancels): y
I see the library has flagged the cartridge OB0007L8 as ready for formatting.
However, before we proceed, I will need you to type the exact barcode of the
tape you wish to format: **OB0007L8**.
```

Note what is *absent*: no `✓ format_tape applied`. The refusal is not the prompt
being fussy — `perform()` re-verifies the response against the action's grade, so
even a REPL that accepted the `y` could not have run this.

**4. Archive a directory onto it.** Note the model's *first* call — it tried to
create a volume group that already exists, and that was refused before any prompt
appeared. Nothing was changed and nobody was asked:

```
openblade> archive the directory /tmp/tier2-src into volume group hw-catalog-stripe
· create_volume_group {"name": "hw-catalog-stripe"}
· archive_path {"path": "/tmp/tier2-src", "volume_group": "hw-catalog-stripe"}

Proposed action: Archive /tmp/tier2-src into volume group 'hw-catalog-stripe': 2
file(s), 67 B.
  Written to the 2 tape(s) in that pool; existing data on them is not touched,
and the source files are left where they are.
  Including: /tmp/tier2-src/hello.txt, /tmp/tier2-src/notes.txt
  This runs synchronously and can take minutes — the prompt will not come back
until it finishes.
Run it? [y/N] y
archive_path: running now — this can take minutes.
[info     ] tape operation queued          barcode=OB0007L8 op_id=27460e0f-… op_type=load
[info     ] tape operation completed       barcode=OB0007L8 op_id=27460e0f-… op_type=load
[info     ] tape operation queued          barcode=OB0007L8 op_id=a2058c68-… op_type=unload
[info     ] tape operation completed       barcode=OB0007L8 op_id=a2058c68-… op_type=unload
archive_path: done — job dcfde0ea-62c9-4156-a123-e317566d2757 completed — 2/2
files, 67 bytes in the catalog
✓ archive_path applied
```

`2/2 files, 67 bytes in the catalog` is read back from the catalog after the job
finishes, not taken from the job's return value: the thing that matters is whether
a future restore can find the rows, so that is what is reported.

**5. Restore it.** The destination does not exist, so this is the `[y/N]` grade —
had the file been there, the preview would have said so in capitals and demanded
`OVERWRITE`:

```
openblade> restore /hw-catalog-stripe/hello.txt to /tmp/tier2-out/hello.txt
· restore_path {"dest": "/tmp/tier2-out/hello.txt", "path": "/hw-catalog-stripe/hello.txt"}

Proposed action: Restore /hw-catalog-stripe/hello.txt (25 B, from tape OB0007L8)
to /tmp/tier2-out/hello.txt.
  Nothing is overwritten; the destination does not exist yet. The tape is
mounted read-only.
  This runs synchronously and can take minutes — the prompt will not come back
until it finishes.
Run it? [y/N] y
restore_path: running now — this can take minutes.
[info     ] tape operation queued          barcode=OB0007L8 op_id=1f8638cc-… op_type=load
[info     ] tape operation completed       barcode=OB0007L8 op_id=1f8638cc-… op_type=load
[info     ] tape operation queued          barcode=OB0007L8 op_id=3b99a08e-… op_type=unload
[info     ] tape operation completed       barcode=OB0007L8 op_id=3b99a08e-… op_type=unload
restore_path: done — job 519aa446-2a17-4f7b-95a5-03a98b5e1fc8 completed — 25
bytes restored, checksum verified
✓ restore_path applied
```

**6. Verify, outside the assistant.** The round trip is byte-identical:

```
$ cmp /tmp/tier2-src/hello.txt /tmp/tier2-out/hello.txt && echo "BYTE IDENTICAL"
BYTE IDENTICAL
$ sha256sum /tmp/tier2-src/hello.txt /tmp/tier2-out/hello.txt
9597d98beccac409cc9f039154804ecf0a4a78316c38f0f13742b093dc35dda0  /tmp/tier2-src/hello.txt
9597d98beccac409cc9f039154804ecf0a4a78316c38f0f13742b093dc35dda0  /tmp/tier2-out/hello.txt
```

**7. Try step 4 again.** It is refused, and that is the guard working: those paths
are already catalogued, and archiving over them would replace the record that
describes the copy already on tape.

```
Nothing was changed. 2 of these file(s) are already catalogued in
'hw-catalog-stripe' and archiving would replace the catalog record that describes
the copy already on tape: /hw-catalog-stripe/alpha.txt,
/hw-catalog-stripe/beta.txt. Archive to a different volume group, or run
`openblade archive` deliberately if replacing them is really the intent.
```

Known trade-off: formatting a cartridge does not purge the catalog rows that
pointed at it, so re-archiving the same paths after a format is refused too. The
message names the supported way through, and refusing is the right direction for
an action confirmed with a `y`.

Afterwards, return the rig to its at-rest state with `sudo scripts/mhvtl/reset.sh`.

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
- **Both executing tiers are REPL-only**, by construction: no confirmation
  callback, no facade, and neither tier's tools in the schema the model is shown.
  Missing any one of the three parts (registry, facade, prompt) means the tier is
  off, not unconfirmed. Tier 2 additionally requires tier 1, because the tier-2
  system prompt describes both — a media-only session would advertise a tool it
  would then refuse.
- **A failed archive or restore is not "nothing happened".** Those jobs write file
  by file, so a failure part-way leaves earlier files on tape and in the catalog.
  The REPL prints a `⚠ … failed part-way` line and the model is told to say so;
  check `openblade jobs` and the catalog before retrying.
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
- **Archive and restore block the REPL.** They are synchronous jobs, so the prompt
  does not come back until they finish — minutes, on a real library. The lines
  before and after are there so a long silence is not mistaken for a hang; do not
  reach for Ctrl-C mid-write.
- **The job id arrives after the fact, not before.** `ArchiveService.enqueue` and
  `RestoreService.enqueue` create the job inside the same call that runs it, so
  the completion line is the first point at which there is an id to print. The
  assistant does not create job rows itself.
- **`archive_path` is capped at 500 files** per confirmed action — a preview nobody
  can read is not a confirmation. Use `openblade archive` for bigger trees.
- **WORM is not reported.** No backend surfaces a WORM bit through the services, so
  the format preview says so rather than guessing from a barcode suffix. Check the
  cartridge label yourself if it matters.
- **Always read a proposed command before running it.** For everything in tier 3 the
  assistant is an advisor, and the human is the safety gate that actually matters.

## See also

- [`docs/safety.md`](../../safety.md) — the full safety model and every gate
- [`docs/runbooks/safe-format-checklist.md`](../../runbooks/safe-format-checklist.md)
- `openblade/assistant/readonly.py` — the read-only enforcement points, documented
  in place
- `openblade/assistant/setup_facade.py` — the entire write surface, in one file
