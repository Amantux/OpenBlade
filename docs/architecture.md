# OpenBlade architecture

## Overview
OpenBlade separates control-plane logic from backend implementation. Domain models and safety policies define shared rules, simulator modules implement a fully in-memory library and LTFS stack, and hardware modules wrap external tools behind explicit guards.

## Components
- **Domain layer**: typed models, state machines, policies, and backend protocols.
- **Simulator backend**: in-memory slots, drives, changer state, LTFS volumes, and fault injection.
- **Hardware backend**: guarded wrappers around changer and LTFS tools with `shell=False` subprocess execution.
- **Catalog**: SQLite-backed metadata for volume groups, file records, file instances, and safety tokens.
- **Jobs**: synchronous queue plus archive/restore/format services that enforce resource ownership.
- **API/CLI**: operator interfaces over the same service graph.
- **FUSE/NAS helpers**: namespace and export helpers built on the catalog.

## Backend abstraction layer
`LibraryBackend` and `LTFSBackend` define the minimum contract required by jobs and interfaces. The simulator implements the same shape as hardware-focused classes, which keeps higher-level workflows backend-agnostic.

## Simulator vs real hardware
The simulator is the default backend and is complete enough for inventory, format, mount, write, read, fault injection, and concurrency tests. Real hardware code is intentionally narrower and guarded by `RealHardwareGuard` so development and CI never depend on devices.

## Safety model
Safety gates live in domain policies and operational workflows:
- real hardware enablement is explicit,
- formatting requires a dry run plus a one-time token,
- unload is blocked while LTFS is mounted or dirty,
- job queue ownership prevents shared drive or changer use.

## Archive flow
1. Choose or assign a tape for the volume group.
2. Load the cartridge into a drive.
3. Mount LTFS read-write.
4. Copy file data.
5. Verify size and checksum against tape stat results.
6. Update catalog records.
7. Cleanly unmount.
8. Unload back to a slot.

Files are only recorded as verified after copy, verification, metadata update, and clean unmount complete.

## Restore flow
1. Resolve the catalog path to a file instance.
2. Load the source cartridge if needed.
3. Mount LTFS read-only.
4. Read to the destination path.
5. Verify checksum.
6. Unmount and unload.

## Catalog design
The catalog uses SQLite tables for volume groups, barcode assignments, file records, file instances, and safety tokens. This keeps CLI and API state persistent across process runs.

## Job system
Jobs are stored in-process with explicit state transitions. The queue additionally tracks changer and drive ownership so concurrent operations cannot claim the same hardware resources.

## Drive leases and recovery
Drive exclusion for tape I/O is a catalog row, not process memory. `DriveScheduler` is a façade over a `LeaseStore`: `CatalogLeaseStore` in production (one per `AppContext`, shared by the API and the CLI through the same `drive_leases` table) and `InMemoryLeaseStore` for unit tests. Each lease carries a monotonic `fencing_token`; job modules call `scheduler.verify(handle)` before a read-write mount, a write batch, and unmount/unload, and `heartbeat` after writes, so a lease that expired or was released behind a running job aborts it with `StaleLeaseError` instead of writing to a drive it no longer owns. On startup `recover_after_restart` fails any job the previous process left `running`, releases its leases, and reports expected-vs-observed drive contents — it never moves media. The report is read-only at `GET /jobs/recovery`. Spec and rationale: `docs/decisions/2026-10-09-persistent-drive-leases.md`.

## FUSE namespace
The FUSE-oriented layer is intentionally thin: catalog entries define the namespace, and hydration delegates to restore workflows. This keeps the namespace authoritative and avoids bypassing safety or verification logic.

## NAS tape-native planning models

Cartridge lifecycle (`openblade/nas/state_machine.py`), LTO media/drive
compatibility and foreign-media detection (`openblade/nas/media.py`), and pure
capacity/replication/export/spanning planners (`openblade/nas/capacity.py`)
sit beside the existing archive and restore planners. They hold no I/O and no
persistence yet; see the "Tape-native domain models" section of
`docs/wiki/guides/fuse-and-nas.md` for the model list and tests.
