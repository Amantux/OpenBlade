# Monitoring — OpenBlade

Metrics are Prometheus text at
`GET /aml/system/emulator/latency/metrics/prometheus` (auth-gated). They are held
**in memory** in `AMLState`, so a restart resets them — this is a known SPOF, and
the alerts below make that reset *observable* rather than a silent dashboard drop.

## Making the monitoring trustworthy

A green board is only trustworthy if (a) a dead component actually turns the board
red and (b) the numbers on it are live, not a frozen/cached body. Two additions
close that gap:

- **Freshness heartbeat** — `openblade_metrics_heartbeat_timestamp_seconds` is the
  unix time the payload was generated. `time() - heartbeat` stays ~0 on healthy
  scrapes and grows only when the body is stale (proxy cache, wedged exporter).
- **Rule ↔ metric verification** — `tests/integration/test_operability_alerts.py`
  asserts every `openblade_*` metric named in any alert rule is one the exporter
  actually declares/emits, so a renamed or typo'd metric can't ship an alert that
  silently never fires. It guards the parity rules too.

## Alert rules

`deploy/emulator/observability/prometheus/`:
- `openblade-parity-alerts.yml` — degraded component, stalled queue, high latency,
  low throughput, expired cleaning media (pre-existing).
- `openblade-operability-alerts.yml` — this phase:
  | Alert | Fires when | Severity |
  |---|---|---|
  | `OpenBladeFleetOffline` | `up{job="openblade-emulator"} == 0` for 2m | critical |
  | `OpenBladeControlPlaneDown` | `up{job="openblade-control-plane"} == 0` for 2m | critical |
  | `OpenBladeTelemetryStale` | heartbeat older than 5m | warning |
  | `OpenBladeTelemetryReset` | `openblade_system_uptime_seconds < 120` | info |

## Required scrape-target labels (set in your prometheus.yml — not shipped here)

The `up`-based rules depend on a `job` label convention:
- `job="openblade-control-plane"` — the OpenBlade API / emulator control plane.
- `job="openblade-emulator"` — each i3 emulator fleet member (one target each).

Both scrape the metrics path above. Without these labels the fleet/control-plane
alerts won't select their targets — verify with `up` in Prometheus after wiring.

## Pending

Durable telemetry (metrics survive restart) and Alertmanager routing are
follow-ups; today the reset is surfaced by `OpenBladeTelemetryReset`, not avoided.
