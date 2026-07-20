# OpenBlade Production-Readiness Roadmap

Response to the external review. Every item is mapped to a phase, with an honest
status marker and a verification step. The organizing correction is the review's
central one:

> **The emulator must not be the source of truth for the client that talks to the
> real i3.** The source of truth is a versioned combination of official contracts,
> sanitized appliance captures, and hardware-in-the-loop results.

## Status legend

- 🟢 **Executable now** — verifiable and fixable in-repo, no appliance needed.
- 🟡 **Structure now, values later** — we can build the shape/scaffold now, but
  exact wire values need the current spec or an appliance capture to certify.
- 🔴 **Needs the appliance** — cannot be executed or truly verified without a
  physical i3 + LTFS rig; delivered as ready-to-run, not run.

Verified against the repo before writing (not asserted from memory):
- Emulator exists (28 `tests/i3` files, `deploy/emulator`, contract-consumed image). ✅
- iBlade parity = 12/17 full, 4 partial, 1 missing; source manual is Rev A (2017). ✅
- `routes_aml_move_medium.py` uses simplified `{elementAddress,elementType}` + `moveClass 0/3`,
  **and the client (`scalar_http/library_backend.py`) builds the same object** → the
  review's "shared-mistake false-green" is real. ✅
- No committed contract oracle: manuals are gitignored copyright material, Rev D 2019 / Rev A 2017. ✅
- `sharded_archive.py:234,251` calls `mark_instance_archived` per-shard inside the write loop → review #6 confirmed real. ✅
- Review #5 (scheduler `drive_id` mutation) **not reproduced**: `jobs/scheduler.py` is a plain allocator with no `_load_barcode`/mutation — re-investigate before "fixing." ⚠️

---

## Phase 0 — Stop the self-referential fidelity (the meta-fix) 🟢🟡

The highest-leverage change: make fidelity claims answer to something outside the
emulator.

1. 🟢 **Relabel the simplified endpoint.** `routes_aml_move_medium.py` currently
   presents a simplified coordinate as the "real i3 dialect." Rename/annotate it as
   an **OpenBlade-native convenience** surface; the strict-AML claim moves behind a
   compatibility profile that must be certified. (Small, honest, unblocks the rest.)
2. 🟡 **Faithful models, as structure.** Introduce `ScalarCoordinate`
   (frame/rack/section/column/row/type) and `moveClass` as an `IntFlag`. Keep the
   convenient integer IDs **only** inside the OpenBlade domain adapter, and preserve
   the original coordinate the i3 returns instead of reducing it to an int. The
   exact `moveClass` bit values (review says Unload=8, not 3) are marked
   **UNVERIFIED** and gated on the spec/appliance — we model the shape, not invent
   the numbers.
3. 🟢🔴 **Compatibility corpus + differential harness.** Create
   `compatibility/<surface>/<firmware>/…` with the case schema the review specifies
   (sanitized request, status, headers, body, role, firmware, module/partition
   config, expected physical transition, timing, manual section, captured-vs-inferred).
   Build a differential test that runs `request → emulator` vs `request → fixture
   oracle`. 🟢 scaffold + harness now; 🔴 real appliance captures later. Separate
   named profiles: **Scalar i3 341G (2026)** and **iBlade Rev A (2017)** — never one
   generic "i3-compatible" label.

## Phase 1 — Verified correctness bugs 🟢 (T3)

4. 🟢 **#6 Atomic sharded commit.** Instances must be `STAGING`/`VERIFYING` until:
   all shards written → all read-back/verified → manifest verified → clean unmount →
   inventory reconciled → **one** catalog commit → mark durable. On lane failure:
   keep staged shards discoverable for resume, never expose as archived, journal the
   failure, never suppress unmount/unload errors (convert to "unknown physical state
   → reconcile"). Regression test the partial-failure path.
5. 🟢 **#4 Protocol-agnostic backends.** Type archive/restore/verify/shard services
   against `LibraryBackend`/`LTFSBackend`, not `Mock*`. Add one behavioral contract
   suite run against every backend pairing (mock, emulator+mock-LTFS, scalar-http+fake-LTFS,
   scsi+fake, and 🔴 real+real on the rig).
6. ⚠️→🟢 **#5 Scheduler lease.** Re-investigate the alleged `drive_id` mutation; if a
   mutation path exists, replace with an immutable `DriveLease` (lease_id, physical
   drive identity, fencing token) and add the reviewer's regression test. If it does
   not exist, add the regression test anyway to pin correct behavior.

## Phase 2 — Persistence, leases, resumability 🟢 (large)

7. 🟢 **#7 Persistent drive leases.** Move `DriveScheduler` off in-memory
   `threading.Condition` to DB-backed leases: owner, op id, physical drive serial,
   barcode, expiry, **monotonic fencing token**, heartbeat, expected vs observed
   physical state. Every destructive op rejects a stale fencing token.
8. 🟢 **Persistent, resumable jobs.** Recover dead background jobs; idempotent
   resume; physical-state reconciliation on restart.

## Phase 3 — Recovery & protection modeling 🟢

9. 🟢 **#11 Reconstructible catalog.** Every committed tape generation carries a
   tape-resident manifest (`tape.json`, versioned manifest, per-tape catalog shard,
   dataset/generation ids, hashes, normalized paths, placement/protection policy,
   sibling tapes required, creator version, schema version, previous-generation
   pointer, **commit marker written last**). CI proves a blank DB rebuilds from
   simulated tape contents **including partially-corrupt and missing tapes**.
10. 🟢 **#8 Separate the axes.** Model placement / striping / protection / failure
    domain / restore quorum independently. `STRIPE`/`BLOCK_STRIPE` are *performance*,
    not durability — a UI "sharded" label must not imply "protected." Add replication
    now; erasure-coding as a later protection option.

## Phase 4 — CI/CD trustworthiness 🟢

11. 🟢 **#A Always-running required aggregation gate** — one required check that
    depends on every applicable job, understands valid skips, fails on any
    required-failure/cancel, prints selection reasoning, and fails if a changed file
    has no owning test category.
12. 🟢 **#B Full-suite on master.** `pytest -m "not real_hardware and not real_i3"`
    on every merge (this repo already got bitten by selective coverage — the safety
    guard rotted). PR = targeted + ownership audit; nightly = slow/stress/fuzz/
    mutation/rebuild.
13. 🟢 **#C Consolidate emulator workflows** into reusable `workflow_call` files.
14. 🟢 **#F Ratchet lint/type** instead of `continue-on-error`: baseline current
    violations, fail new ones, require changed files clean, shrink the baseline;
    require protocol/safety/scheduler/sharding modules fully typed immediately.
15. 🟢 **#G Workflow hardening:** actionlint, zizmor, shellcheck, yamllint; pin
    actions to SHAs; minimal permissions; OIDC for deploy creds; no repo-scope
    long-lived hardware/deploy secrets.
16. 🟡 **#H Differential + mutation tests** around safety gates, barcode allowlists,
    state transitions, moveClass, coordinate conversion, checksum, "mark archived"
    timing, lease fencing, compensation paths. (Differential needs Phase 0 oracle.)
17. 🟢 **#I Async timing profiles** with a virtual clock: instant / normal /
    slow-robotics / busy-library / intermittent-drive / session-expiry / rebooting /
    degraded-media.

## Phase 5 — Auth fidelity (#2) 🟡

18. 🟡 Cookie-session support (not just Bearer), form/object login, logout, expired
    sessions, invalid creds, default-password-required, MFA-required, max-sessions,
    JSON/XML, missing/malformed Accept/Content-Type, and 401/403/404/412/429/500/503
    handling — authored as compatibility tests; exact behavior certified against the
    spec/appliance.

## Phase 6 — NAS data plane (#9, #10) 🟢🟡

19. 🟢 **Real protocol tests:** containerized Samba + Linux SMB client and
    NFS-Ganesha + Linux NFS client; exercise listing, open/read/seek/close,
    partial-range, rename/delete, locking, ACL mapping, case sensitivity, unicode/long
    paths, sparse files, interrupted connections, hydration blocking/timeout,
    offline-tape errors, concurrent opens of an offline file, eviction-while-open,
    server-restart-during-hydration.
20. 🟢 **Tape-native NAS domain models:** online/offline/degraded/hydrating/
    exporting/sequestered/repair-required; scratch thresholds; foreign media; VG
    replication state; fragmentation; capacity reservations; file spanning;
    export/import sets; vault; media generation ↔ drive compatibility; required
    drives / estimated swaps; small-file aggregation. Add semantic + negative tests
    for assignment/merge/export-prep/repair/replication (not endpoint-presence).
21. 🟡 Complete the FUSE data plane (currently a stub) behind the layered design:
    SMB/NFS → namespace/metadata → hydration/cache → archive/restore engine →
    placement/robotics.

## Phase 7 — Hardware-in-the-loop 🔴 (needs the appliance)

22. 🔴 **#D self-hosted i3/LTFS runner:** nightly read-only lane (login/logout,
    firmware/capability discovery, inventory, element coordinates, partition
    visibility, drive-serial/`/dev/st` correlation, TapeAlert/health, non-mutating
    reports, **emulator-vs-appliance differential**); weekly/manual destructive lane
    (GitHub Environment approval, allowlisted sacrificial barcodes, snapshot
    before/after, load/unload, LTFS format, write/read/checksum, reboot persistence,
    sharded write+restore, safe failure injection, guaranteed recovery).
23. 🔴 **#E Physical drive-identity bridge:** certify AML coordinate ↔ serial ↔ SCSI
    inquiry ↔ `/dev/tape/by-id` ↔ LTFS mount on the rig before enabling any real
    write path. Never rely on transient `/dev/st0` ordering. (`scalar_http` currently
    raises `NotImplementedError` here — correct until proven on metal.)

## Phase 8 — Release pipeline (P2) 🟢🟡

24. 🟢🟡 Build wheel+container **once**; SBOM; dependency + container vuln scan; sign
    + provenance; deploy that exact **digest** to staging; run emulator/NAS/rebuild/
    restore suites; require approval for production; deploy by digest; post-deploy
    topology + safe read-only appliance checks; auto-roll-back **application code**
    on failure — **never** auto-roll-back physical tape state (initiate reconciliation).

---

## Execution order (what I'll actually do, and in what sequence)

Following the review's own priority ordering, front-loading verified in-repo fixes:

1. **Phase 1 #6 (atomic commit)** + **#4 (protocol backends)** — verified real bugs, safety-relevant. *(first PRs)*
2. **Phase 0** relabel + corpus/differential scaffold + faithful model shapes.
3. **Phase 4** CI aggregation gate + full-master run + workflow hardening + lint/type ratchet.
4. **Phase 3** reconstructible catalog + protection-axis modeling.
5. **Phase 2** persistent leases/jobs + fencing.
6. **Phase 6** real SMB/NFS harness + tape-native NAS models.
7. **Phase 5** auth-fidelity compatibility tests.
8. **Phase 8** release pipeline.
9. **Phase 7** 🔴 authored, run only when a rig exists.

Each phase ships as its own PR(s), verified green, T3 items through the adversarial
reviewer. Hardware-gated items (🔴) are delivered ready-to-run and clearly marked
`pending appliance`, never faked green.
