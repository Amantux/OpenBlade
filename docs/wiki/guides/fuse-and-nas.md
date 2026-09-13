# FUSE & the NAS namespace

**Short version: one thing is mountable — `openblade fuse mount`, read-only,
over the *catalog* namespace.** There is still no SMB server and no NFS server,
and the NAS-side namespace (`/virtual/*`) is a browse API, not a mount.

This page stays blunt about the gaps, because "FUSE namespace" used to lead
operators to go looking for a mount point that did not exist. Now one exists —
and it is much smaller than the phrase suggests, so the limits are spelled out.

---

## `openblade fuse mount` — what it actually does

```
openblade fuse mount /mnt/openblade [--hydrate] [--allow-other] [--verbose]
```

Runs in the **foreground**; unmount with `fusermount -u /mnt/openblade` from
another shell, or Ctrl-C. Requires the optional extra and host FUSE support:

```
pip install 'openblade[fuse]'      # fusepy; needs libfuse2 and /dev/fuse
```

Without the extra the command prints an install hint and exits 1 — it does not
traceback and does not half-mount anything.

**fusepy, not pyfuse3.** fusepy is pure Python (ctypes over libfuse2), so there
is no compiler or Cython at install time, and it is synchronous — which lets the
mount call the existing catalog and restore code unchanged. pyfuse3 would drag
in a build step and an async runtime for no benefit at this size.

### Semantics v1

| Operation | Behaviour |
|---|---|
| Directory tree | The catalog `file_records` path namespace, served by the same `CatalogFilesystem` that backs `openblade catalog`. Nothing new decides what a path is. |
| `stat` | From catalog metadata: `st_size` = recorded size, times = the record's `created_at`, mode `0555` for directories and `0444` for files. No write bit anywhere. |
| `read` of a **cached** file | Served from the hydration cache, checksum-verified by the cache on the way out. |
| `read` of an **uncached** file | **`EIO`**, plus a log line naming the fix: `openblade restore --path <path> --to <destination>`. Nothing is queued — the mount never starts tape motion behind your back. |
| `read` of an uncached file **with `--hydrate`** | The read **blocks** while the restore service loads, mounts, reads and unmounts the tape, then the bytes are cached and served. |
| Every write operation | **`EROFS`** — `write`, `create`, `mkdir`, `rmdir`, `unlink`, `rename`, `symlink`, `link`, `truncate`, `chmod`, `chown`, `utimens`, `setxattr`, `removexattr`, `mknod`. The mount is also mounted `ro`, so the kernel refuses most of them before the handler is reached; the handlers exist so the refusal is explicit rather than an `ENOSYS`. |

### The `--hydrate` tradeoff (default: **off**)

With `--hydrate`, a `read()` can block for a full load → mount → read → unmount
cycle: seconds on the simulator, **tens of seconds per file on real hardware**
(and `openblade restore` load/unloads per file — see the restore notes). A
`read()` is issued by anything that touches the file: `ls` previewers, desktop
thumbnailers, `grep -r`, backup agents, your editor's file watcher. One stray
recursive tool can therefore queue hundreds of tape movements.

That is why the default is off and the default answer is `EIO`: a fast, honest
error that names the command to run beats an unbounded stall.

Other limits, stated rather than hidden:

- **Whole files are buffered in memory** on open. Same limitation as
  `RealLTFSBackend.write_file`; fine for documents, wrong for multi-TB media.
- **`--allow-other` is off by default.** It exposes the mount to every user on
  the host and additionally needs `user_allow_other` in `/etc/fuse.conf`.
- **No caching of the tree**: every `readdir`/`getattr` hits the catalog.
- The mount serves the **catalog** namespace (`file_records`), *not* the NAS
  namespace (`nas_datasets`) described further down. They are still two
  unconnected halves.

---

## What still does not exist

| Thing | Status |
|---|---|
| `OPENBLADE_FUSE_MOUNT_POINT` | The config field `fuse_mount_point` exists, is **read by nothing**, and has no environment override. `openblade fuse mount` takes the mount point as an argument. |
| A FUSE mount of the **NAS** namespace | **No.** Only the catalog namespace is mountable. |
| An SMB (Samba) server | **No.** `openblade/nas/samba.py` is a 15-line class whose only method renders a 3-line `smb.conf` stanza. It is referenced by nothing — not by the app, not by tests. |
| An NFS server | **No.** Same shape: `nfs.py` renders one `/etc/exports` line and is referenced by nothing. |
| An SFTP listener | **Effectively no.** See below. |
| `libfuse` in the shipped image | **No.** The extra and `libfuse2` are a host-side install; the container does not ship them. |

The `nas_shares` table and the `/nas/shares` CRUD endpoints store share
definitions as **metadata only**. Nothing renders or applies them.

The code itself says so — `openblade/nas/fuse_hook.py`:

> Stub for optional FUSE virtual filesystem integration. In v1, this is a no-op
> stub that records access attempts and returns appropriate offline/hydrating
> error codes **without mounting anything**.

---

## What does exist

### `CatalogFilesystem` — the CLI's namespace

An in-process, read-only view over `file_records`. `listdir`, `stat`, `read`;
`write` and `delete` raise `PermissionError("OpenBlade virtual filesystem is
read-only")`.

This is what backs `openblade catalog <path>` **and the FUSE mount above** — the
kernel-facing layer in `openblade/fuse/mount.py` only translates it into FUSE
operations and error numbers. It is not exposed over HTTP.

A read of a file whose content is not in the hydration cache raises:

```
CartridgeOfflineError: <path> is offline; queue hydration before reading
through the virtual filesystem
```

It does **not** trigger a recall. Cache hits are integrity-checked against the
stored SHA-256, and the cache entry is deleted on mismatch.

### `VirtualFilesystem` — the HTTP namespace

A read-only browse tree over the **NAS** catalog (`nas_datasets`,
`nas_file_records`, `path_mappings`), with the shape:

```
/pools/<pool>/<dataset_id>/<relative path>
```

Exposed at `GET /virtual/ls`, `GET /virtual/stat`, `POST /virtual/hydrate`,
`GET /virtual/jobs`, `GET /virtual/jobs/{id}`, `DELETE /virtual/jobs/{id}`. All
require authentication. Path traversal (`..`) is rejected.

---

## Hydration: three implementations, only one moves bytes

This is the confusing part. "Hydration" means three different things depending on
which module you are in.

| Implementation | What it does |
|---|---|
| `openblade/fuse/hydration.py` | A bare list with `enqueue`/`pending`/`clear`. Never instantiated in production. A stub. |
| `VirtualFilesystem.request_hydration` (`POST /virtual/hydrate`) | **Bookkeeping only.** Marks the file `HYDRATING`, creates an in-memory job with status `queued`, returns. **Nothing ever executes it.** The jobs live in a per-instance dict and never advance past `queued` except by cancellation. Files left marked `hydrating` stay that way. The route docstrings call it a "mock hydration job". |
| `openblade/nas/hydration.py` — `HydrationExecutor` | The real one. Reads bytes off tape and writes them out. |

If you want data back, the executor is the path that works:

- `run(job_id)` requires status `QUEUED`, then per file: mark `HYDRATING`,
  materialise, mark `ONLINE_CACHED` with a `cache_path`.
- Bytes land at `$OPENBLADE_RESTORE_DIR/{file_id}`, default
  **`/tmp/openblade-restore`** — note this reads the environment variable
  directly with a different default from `config.restore_dir`
  (`~/.openblade/restore`).
- Operator controls: `cancel`, `pause`, `resume`, `retry` (from `FAILED` only).
  Status set: `queued, planning, running, paused, completed, failed, cancelled`,
  plus a `partial_success` flag.

### ⚠️ The placeholder-content trap

`HydrationExecutor` is typed against the **mock** LTFS backend and its docstring
says it executes "against the simulator without real filesystem I/O". If the tape
read fails for any reason, it falls back to **placeholder bytes** of the form
`HYDRATED:<path>:<barcode>`.

What saves you is `_verify_restored_integrity`, which hashes the materialised
bytes against the recorded SHA-256 and fails the file. **But that only works if
the record carries a real checksum.** If `checksum_sha256` is empty, the
placeholder passes through as a successful hydration.

Do not treat NAS hydration as production-grade data recovery. For real restores
use [the classic restore path](restoring.md), which has no placeholder fallback.

---

## What a read of a non-cached file does — four different answers

| Surface | Behaviour |
|---|---|
| `openblade fuse mount` | `EIO` + a log line naming `openblade restore`; with `--hydrate`, blocks and restores. |
| `CatalogFilesystem.read` | Raises `CartridgeOfflineError`. Does not trigger a recall. |
| `POST /nas/fuse/open` | Returns a **dict describing what a FUSE layer should do**: `{"action": "queue_hydration", "message": "File is offline. Hydration queued.", "tape_barcode": …}`. ⚠️ **It queues nothing.** The message is wrong about its own behaviour; the call only appends to an access log readable at `GET /nas/fuse/log`. (Also reachable as `/storage/nas/fuse/open` — the NAS router is mounted twice.) |
| Mock SFTP session | Raises `OfflineFileError` or returns stub content. |

---

## SFTP

`ProtocolGateway` does real work in places — PBKDF2-SHA256 credentials at 200k
iterations, path allow-listing, inbox routing, session accounting. But `start()`
merely checks that `asyncssh` imports, **binds a socket and immediately closes
it**, then sets status `RUNNING`. No SSH/SFTP listener is ever served.

`asyncssh` is a **dev-only** dependency, so in a production image `start()`
raises:

```
SFTP gateway backend is unavailable because asyncssh is not installed
```

Variables: `OPENBLADE_SFTP_ENABLED` (off by default), `OPENBLADE_SFTP_HOST`
(`0.0.0.0`), `OPENBLADE_SFTP_PORT` (`2222`), `OPENBLADE_SFTP_MAX_SESSIONS`
(`10`), `OPENBLADE_INBOX_ROOT` (`/var/lib/openblade`).

---

## The sidecar policy file

`openblade/nas/sidecar.py` is real and works: it reads `.openblade-policy.yaml`
from a directory, validates it, and warns on unknown keys. It is a
policy-resolution helper for ingest and has nothing to do with mounting.

---

## Doc corrections

- `docs/fuse.md` calls this "a lightweight namespace abstraction over the
  catalog" and says future kernel FUSE work can reuse the same contracts. That
  is now what happened — but the page still reads as if nothing is mountable,
  and should point at `openblade fuse mount`.
- `docs/wiki/README.md` still summarises this page as "what is mountable today
  (nothing)". Stale as of the read-only catalog mount.
- `docs/runbooks/real-data-campaign.md` §"Does not exist" lists the FUSE mount
  and drive-health/TapeAlert as unimplemented. Both now exist; the campaign's
  other entries there are unchanged.
- `docs/production-readiness-roadmap.md` calls the FUSE data plane "a stub".
  The *mount* is no longer a stub; `openblade/nas/fuse_hook.py` still is.
- `docs/architecture.md` says hydration "delegates to restore workflows". It does
  not: `virtual_fs.request_hydration` delegates to nothing, and the NAS executor
  is a separate path from the classic restore job.
- `docs/architecture.md` lists "export helpers" among the FUSE/NAS helpers. Those
  helpers (`samba.py`, `nfs.py`) are unreferenced dead code.
- `docs/production-readiness-roadmap.md` correctly calls the FUSE data plane "a
  stub", which contradicts `docs/architecture.md`'s more confident framing. The
  roadmap is right.

---

## Related

- [The catalog](the-catalog.md) — the two independent catalogs
- [Restoring](restoring.md) — the path that actually returns your data
- `docs/fuse.md`
