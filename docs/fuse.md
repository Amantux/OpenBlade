# FUSE data plane

Five layers, top to bottom, and what is real today:

| Layer | Status |
|---|---|
| SMB/NFS servers | **Not shipped in the image.** See `deploy/nas-protocols` (protocol rig). They hydrate through `ProtocolGateway.on_open/on_read_range/on_close/on_evict` (`openblade/nas/protocol_gateway.py`). |
| Namespace/metadata | **Real.** `CatalogFuseOperations` (`openblade/fuse/mount.py`): offline files report their real size; xattr `user.openblade.state` = `offline`/`hydrating`/`online`. |
| Hydration/cache | **Real.** `Hydrator` (`openblade/fuse/hydration.py`): one shared ticket per path, `OPENBLADE_FUSE_HYDRATE_TIMEOUT` (default 300 s) blocking wait, or `EAGAIN` with `blocking=False`; failure = `EIO`, file stays `offline`, reason in `last_error()`. `HydrationCache` (`cache.py`): optional byte budget with LRU eviction that never evicts a file with open handles; restores land under a temp name and are renamed only after the sha256 matches. |
| Archive/restore engine | **Real, via jobs.** `JobRestoreEngine` calls `RestoreService.enqueue` (one restore job per file); never a tape backend directly. A restarted hydrator re-attaches to a pending/running restore job for the path. Every `restore_batch`/`resume` call runs on a Hydrator worker thread over its **own** catalog Session (`catalog_factory`), never the caller's. Files of one tape batch are still restored one job per file (not yet one load per tape). |
| Placement/robotics | **Partial.** Requests are batched per tape barcode for `OPENBLADE_FUSE_BATCH_WINDOW_MS` (default 250 ms) and restored back to back; whether that is one physical load is the jobs scheduler's decision. |

Unmount (`destroy`) cancels batches not yet started and waits for in-flight restores.
`openblade fuse mount --hydrate` builds this data plane (`build_data_plane` in `openblade/cli/fuse_and_health.py`) and passes it as `data_plane=`. Timeout and batch window come from the settings fields `fuse_hydrate_timeout_s` / `fuse_batch_window_ms` (env `OPENBLADE_FUSE_HYDRATE_TIMEOUT` / `OPENBLADE_FUSE_BATCH_WINDOW_MS`). The cache byte budget (`max_bytes`) is not yet configurable from the CLI, so the mount's cache is unbounded.
