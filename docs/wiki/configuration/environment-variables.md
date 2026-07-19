---
title: Environment Variables
document_type: configuration
status: verified
last_verified: 2026-07-19
verified_against: [openblade/config.py, docker-compose.yml, deploy/emulator/docker-compose.standalone.yml, openblade/assistant/config.py]
owners: [platform]
tags: [configuration, env]
---

# Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `OPENBLADE_BACKEND` | `mock`/`simulator` or `real` | `mock` |
| `OPENBLADE_REAL_HARDWARE_ENABLED` | gate for real hardware | `false` |
| `OPENBLADE_ROBOTICS_TRANSPORT` | `scsi` or `webservices` (when real) | `scsi` |
| `OPENBLADE_DB_URL` / `OPENBLADE_DB_PATH` | SQLite location | `~/.openblade/openblade.db` / `/data/openblade.db` |
| `OPENBLADE_SCALAR_API_ONLY` | emulator-only mode | `false` |
| `OPENBLADE_IBLADE_COMPAT_MODE` | `strict`/`extended` | `extended` |
| `OPENBLADE_EMULATOR_URLS` | controller→emulator targets | localhost:8010-8012 |
| `OPENBLADE_SCALAR_URL/USER/PASSWORD/VERIFY_TLS` | real-i3 webservices target | — |
| `OPENBLADE_ENV` | `development`/`production` | `development` |
| `OPENBLADE_ASSISTANT_BASE_URL/MODEL/API_KEY/ENABLED` | assistant endpoint | off |
| `OPENBLADE_WEB_BACKEND_URL` | Flask UI → api | `http://127.0.0.1:8000` |
| `EMULATOR_PROFILE/SLOT_COUNT/DRIVE_COUNT/OCCUPANCY_PERCENT/LATENCY_PROFILE` | emulator shape | `scalar-i3-50-3` etc. |
| `OPENBLADE_AGENT_WRITE_ENABLED` | **proposed** agent write kill-switch | disabled |

Secrets among these (API keys, passwords) — see [secrets](secrets.md).
