# Single Points of Failure — OpenBlade

OpenBlade is a single-process, single-node controller by design (simulator-first,
one FastAPI app, SQLite, in-process jobs). That is appropriate for its scope, but
it means a few components have no redundancy: if one fails, a whole capability is
lost until it is recovered. This document names them, states the blast radius, and
points at the mitigation that exists today versus the residual risk.

Scope note: this is an honest inventory, not a claim that OpenBlade is
highly-available. Making it HA is a larger effort; the value here is knowing
exactly what breaks, how visibly, and how to recover.

## SPOF-1 — The SQLite catalog (one file)

- **What:** all durable state (datasets, file records, jobs, cartridges, RBAC,
  path mappings) lives in one SQLite file — `OPENBLADE_DB_URL`, default
  `sqlite:///~/.openblade/openblade.db` (`openblade/config.py`). No replica, no
  failover.
- **Blast radius:** file loss/corruption loses the catalog → inventory and job
  history are gone; tape *content* survives on media but must be re-indexed.
- **Mitigation (shipped):** WAL-safe backup + **executed** restore verification —
  `scripts/backup_db.py`, `scripts/restore_verify.py`, and the runbook in
  `docs/disaster-recovery.md`. Restore is verified on an isolated copy, so a
  backup you can't actually restore is caught before you need it.
- **Residual risk:** recovery is manual and only as fresh as the last backup;
  there is no continuous replication. Next mitigation: scheduled backup rotation +
  off-host copy (a backup on the same host does not survive host loss).

## SPOF-2 — The single application process (in-process jobs)

- **What:** one process serves the API and runs archive/restore work via an
  in-process `JobQueue` + a single `Worker` (`openblade/jobs/`,
  `openblade/bootstrap.py` builds one `Worker(queue)`). There is no external
  broker and no second instance.
- **Blast radius:** a crash or restart drops in-flight jobs and makes every
  surface unavailable until the process is back. Because there is no broker,
  queued work is not durable across a restart.
- **Mitigation (shipped):** the destructive-op safety model is two-phase
  (dry-run → explicit barcode + one-time token) and archive records only
  verified-after-clean-unmount state, so a mid-op crash does not corrupt tape or
  falsely record success — it fails safe. `/healthz` + `scripts/verify_topology.py`
  make "process/topology healthy" checkable after a restart.
- **Residual risk:** no automatic failover or job resumption. Run it under a
  process supervisor (systemd/container restart policy) so a crash restarts
  promptly; treat in-flight jobs as needing re-issue after an unclean restart.

## SPOF-3 — In-memory emulator/telemetry state (`AMLState`)

- **What:** the emulator control-plane state, audit log, login sessions, and the
  Prometheus **metrics** are held in a module-singleton `AMLState` in memory
  (`openblade/api/aml_state.py`: `_STATE = AMLState()`), rebuilt on boot. Metrics
  are not durable.
- **Blast radius:** a restart zeroes metrics history and drops sessions; a
  dashboard drop can be misread as a real regression.
- **Mitigation (shipped):** the reset is made **observable** — the
  `OpenBladeTelemetryReset` alert (uptime < 120s) and the
  `openblade_metrics_heartbeat_timestamp_seconds` freshness metric
  (`docs/monitoring.md`) distinguish "restarted, metrics reset" from "telemetry
  frozen." So the loss is surfaced, not silent.
- **Residual risk:** metrics history is still lost on restart. Next mitigation:
  durable telemetry (scrape into an external TSDB, which the Prometheus setup
  already implies) so history survives the process.

## Cross-cutting: the emulator fleet

Fleet members (`OPENBLADE_EMULATOR_URLS`) are independent processes; one going
offline degrades only fleet features, and `OpenBladeFleetOffline`
(`up{job="openblade-emulator"} == 0`) alerts on it. This is the one place
OpenBlade already tolerates a single-member failure without losing the control
plane.

## Summary

| SPOF | Loses | Detect | Recover | Residual |
|---|---|---|---|---|
| SQLite catalog | durable state | restore-verify / integrity | restore from backup | manual; off-host rotation TODO |
| App process | availability + in-flight jobs | `/healthz`, topology | supervisor restart | no failover / resume |
| `AMLState` | metrics/sessions | `TelemetryReset`, heartbeat | rebuilt on boot | durable telemetry TODO |
| Fleet member | fleet features only | `FleetOffline` | restart member | already isolated |

The first three are the load-bearing risks; the DR (SPOF-1) and observability
(SPOF-3) mitigations ship with the operability-hardening work. Removing the
availability SPOF (SPOF-2) is deliberately out of scope and would require a
durable queue + a second instance.
