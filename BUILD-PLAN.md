# Production Operability Hardening — Build Plan

Status: Phase 1 landed (this PR). Phases 2–5 scoped below.

## 0. Reality reconciliation (read this first)

The originating task described a Neo4j + Redis + message-broker + Cypher +
CSP-nonce + multi-LLM system. **OpenBlade is none of those.** Verified against the
tree, not memory:

| Task assumed | OpenBlade actually is | Evidence |
|---|---|---|
| Neo4j / Cypher graph DB | SQLite via SQLAlchemy 2.0 async (`aiosqlite`) | `openblade/catalog/db.py`; no `neo4j`/`cypher` anywhere |
| Redis | none | no `redis` dep; `redis` string matches are inside `hardwa**reDis**abled` |
| Message broker + queue workers | in-process `JobQueue` + `Worker`, synchronous `enqueue` | `openblade/jobs/` |
| Periodic scheduler | `DriveScheduler` = drive *allocation* for sharding, not cron | `openblade/nas/` |
| CSP nonce middleware | none | no CSP header code |
| Multi-LLM router | single optional OpenAI-compatible assistant | `openblade/assistant/` |

The 5 priorities were therefore **mapped to OpenBlade's real stack** (user
approved "Adapt to OpenBlade & execute"). No Neo4j/Redis/broker was invented. The
original safety guardrails still hold and are honored: no production data in CI,
never a production restore in testing, auth/parameterization/secure-cookie
behavior left intact, production-access items marked pending.

## 1. Priority → OpenBlade mapping

1. **CI quality gate** → deterministic `operability` workflow that boots the app
   in-process and proves it is deployable + wired + recoverable.
2. **Monitoring trustworthiness** → detect stale heartbeats / zero-consumer
   analogs (topology gate today; heartbeat + alert rules in Phase 2).
3. **Automated/validated deployment** → the same config + topology gates run as a
   pre/post-deploy check (Phase 3 wires them into a deploy script).
4. **Disaster-recovery verification** → executed SQLite backup + restore-verify.
5. **Reduce SPOF** → documented in Phase 5 (single SQLite file, in-memory
   `AMLState`, single process).

## 2. Phase 1 — landed in this PR (all executed, all green)

Three executable gates + tests, wired into CI. Commands are reproducible locally.

| Component | File | Proves |
|---|---|---|
| Config validator | `openblade/config_validation.py` | production refuses to start on default admin password / default service token / missing secrets / missing DB url; dev stays deployable |
| Config CLI | `scripts/validate_config.py` | exit 1 on any blocking finding; also loads real config to confirm shape |
| Topology verifier | `openblade/topology.py` | required endpoints actually respond (probed, not introspected); in-process worker + services + backends are wired; fleet configured (advisory) |
| Topology CLI | `scripts/verify_topology.py` | in-process probe via TestClient; no ports opened |
| DR backup/restore | `openblade/dr.py` | WAL-safe online backup; restore into an **isolated copy** + `integrity_check` + schema + real data-layer read |
| DR CLIs | `scripts/backup_db.py`, `scripts/restore_verify.py` | operator-runnable backup + verification; corrupt/incomplete backup is *reported*, not crashed |
| CI workflow | `.github/workflows/operability.yml` | runs all of the above on every relevant change; includes a negative control that fails if the validator ever passes an unsafe prod profile |

Tests: `tests/unit/test_config_validation.py` (8), `tests/unit/test_topology.py`
(6), `tests/unit/test_dr.py` (5) — 18 passing, ruff + mypy clean. Each test file
reproduces a failure class (prod-with-dev-settings, unregistered endpoint, unwired
worker, corrupt backup, missing backup) and asserts it is caught.

### Reproduce locally
```
pytest tests/unit/test_config_validation.py tests/unit/test_topology.py tests/unit/test_dr.py -q
python scripts/validate_config.py                       # dev: DEPLOYABLE (exit 0)
OPENBLADE_ENV=production python scripts/validate_config.py   # BLOCKED (exit 1)
python scripts/verify_topology.py                       # topology: OK (exit 0)
python scripts/restore_verify.py                        # restore verification: PASS (exit 0)
```

## 3. Phases 2–5 — scoped, not yet built

- **P2 monitoring:** durable-ish heartbeat for the in-process worker + emulator
  fleet; alert rules for `fleet-offline` and `telemetry-stale` alongside the
  existing 5 rules in `deploy/emulator/observability/`. Monitoring today is
  in-memory (`AMLState`) — flag: a restart zeroes metrics (a SPOF, see P5).
- **P3 deployment:** a deploy script that runs `validate_config` (pre) and
  `verify_topology` (post) and refuses to promote on a blocking finding.
- **P4 DR (extend):** scheduled backup rotation + a **real** production-backup
  restore drill — PENDING production access (never run against prod data in CI).
- **P5 SPOF:** the single SQLite file, single app process, and in-memory emulator
  state are the three critical single points of failure. Document blast radius +
  the backup/restore recovery procedure (P4) as the mitigation for the first.

## 4. Guardrails honored

No production data or credentials in CI. Restore verification runs only on an
isolated copy in a temp dir and never touches the source DB. No admin/DB ports
exposed. Auth, cookie-security, and query parameterization untouched.
