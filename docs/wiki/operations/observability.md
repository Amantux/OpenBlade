---
title: Observability
document_type: operations
status: verified
last_verified: 2026-07-19
verified_against:
  - openblade/api/routes_aml_system.py:1420
  - openblade/api/routes_aml_system.py:3303
  - openblade/api/main.py:171
  - openblade/bootstrap.py:33
  - deploy/emulator/observability/prometheus/openblade-parity-alerts.yml
owners: [platform]
tags: [observability, metrics, logs, alerts, agent]
---

# Observability

## Metrics (`verified`) — 14 emitted, all `openblade_*`

Endpoint: `GET /aml/system/emulator/latency/metrics/prometheus`
(`routes_aml_system.py:3303`), `text/plain; version=0.0.4`. **Auth required** —
the Prometheus scrape config MUST send a bearer token (a scrape gap; see
[gap report](observability-gap-report.md)). Metrics are **hand-rolled** and
**in-memory** (reset on restart).

| Metric | Type | Labels |
|---|---|---|
| `openblade_system_uptime_seconds` | gauge | — |
| `openblade_component_status` | gauge | `component` (`network`,`services`,`service:<name>`) |
| `openblade_iblade_request_total` | counter* | `endpoint`,`method`,`operation_class` |
| `openblade_iblade_request_duration_ms` | gauge | `endpoint`,`method`,`operation_class`,`stat`(avg/min/max) |
| `openblade_iblade_request_simulated_delay_ms` | gauge | `endpoint`,`method`,`operation_class`,`stat` |
| `openblade_jobs_state_total` | gauge | `queue`(active/history),`state` |
| `openblade_transfer_activity_total` | gauge | `metric`(e.g. `active_mounts`) |
| `openblade_transfer_throughput_files_per_second` | gauge | `operation`(archive/restore) |
| `openblade_transfer_throughput_bytes_per_second` | gauge | `operation` |
| `openblade_media_utilization_percent` | gauge | — |
| `openblade_media_capacity_bytes` | gauge | `metric` |
| `openblade_drive_state_total` | gauge | `state` |
| `openblade_cleaning_media_total` | gauge | `metric`(assigned/expired_reports) |
| `openblade_storage_volume_usage_percent` | gauge | `volume` |

\* declared `counter` but snapshot/resettable — treat as gauge-like.

Per-request capture is via the `apply_aml_emulator_latency` middleware
(`main.py:171`), which records **only `/aml` and `/iblade` paths**.

## Alerts (`verified`) — `deploy/emulator/observability/prometheus/openblade-parity-alerts.yml`

| Alert | Expr (summary) | for | Failure ID |
|---|---|---|---|
| `OpenBladeServiceDegraded` | `openblade_component_status < 1` | 5m | HEALTH-001 |
| `OpenBladeQueueStalled` | active jobs > 0 AND `active_mounts` == 0 | 10m | JOB-001 |
| `OpenBladeHighLatency` | `max(openblade_iblade_request_duration_ms{stat=avg}) > 1500` | 10m | API-002 |
| `OpenBladeLowThroughput` | `sum(...files_per_second) < 0.001` | 15m | — |
| `OpenBladeCleaningExpired` | `openblade_cleaning_media_total{metric=expired_reports} > 0` | 5m | — |

All alert metric names/labels **match the emitter** (verified — no naming drift).
A Grafana dashboard (`grafana/dashboards/openblade-parity-overview.json`) uses the
same metrics.

## Logging (`verified`)

`structlog` with **default configuration** (`bootstrap.py:33` — `structlog.configure()`
with no args → ConsoleRenderer to stdout). Fields: `event`, `level`, ISO `timestamp`,
plus per-call bound kwargs. **No** `service`/`request_id`/`correlation_id`; **no**
request/access-log middleware. Useful log patterns are cataloged per failure in
[`failure-taxonomy.yaml`](failure-taxonomy.yaml).

## Audit / events (`verified`, in-memory)

`_record_audit` (capped 1000) and `login_activity` (capped 500) on the `AMLState`
singleton; exposed at `GET /aml/…/audit` and login/session routes. **Non-durable.**

## Tracing

None. No OpenTelemetry anywhere (`verified` absence).
