# OpenBlade Shipping Strategy — Expert Debate Summary

Four product experts reviewed OpenBlade and debated how to ship it as a complete solution.
This document captures the key decisions and synthesis.

---

## Expert Panel

| Expert | Focus |
|---|---|
| DevOps Engineer | Deployment, packaging, observability, CI/CD, HA, security |
| UX Designer | Dashboard design, job UX, error communication, sharding UX |
| Hardware Engineer | Device enumeration, LTFS reliability, failure modes, recovery |
| OSS Strategist | Positioning, v1.0 scope, community, docs, commercial potential |

---

## 1. Positioning

> **"An open, modern control plane for LTFS tape libraries on commodity Linux — API-first, simulator-first, and safer to operate than vendor blades."**

**Differentiation vs. competitors:**
- **Bacula / Bareos / Amanda** — backup orchestration suites; OpenBlade is a tape *controller*, not a backup policy engine
- **IBM Spectrum Protect / Veeam** — enterprise data protection platforms; over-scoped and expensive for tape appliance use cases
- **Quantum Scalar i3 / iBlade** — proprietary, $15–50k; OpenBlade is the open alternative

**Primary users:** Storage admins, universities, labs, media studios, archives  
**Secondary users:** Homelabbers, MSPs, open-source infra enthusiasts  
**Tagline:** *"OpenBlade: open-source tape library control for LTFS archives."*

---

## 2. v1.0 Scope Decision

### Must-ship for v1.0
- Mock simulator (fully supported, documented)
- Real hardware read-only inventory
- Safe tape format (dry-run + token confirmation)
- Archive + restore jobs with checksums
- Catalog persistence (SQLite)
- Volume groups / media assignment
- CLI + FastAPI with OpenAPI docs
- Docker-based demo path
- Operator runbooks (import, dirty unmount, failed drive, safe format)

### Explicitly cut from v1.0
- Policy engine / scheduling matrix
- Multi-node HA / clustering
- RBAC / SSO / LDAP
- Cloud control plane
- Dedup / compression
- Broad "supports all libraries" claims

**OSS verdict:** Don't block v1.0 on a mature React UI. Ship CLI + API + Swagger first; React dashboard is v1.1.

---

## 3. Deployment Model

**Bare-metal + systemd is the primary production target.** Docker is for dev/CI/mock only.

**Why containers are wrong for real tape hardware:**
- LTFS + FUSE mounts are awkward in containers
- Real tape access needs privileged `--device` + `--cap-add SYS_ADMIN`
- At that point, container isolation is theater

### Production layout (bare-metal)
```
/etc/openblade/openblade.env        # EnvironmentFile for systemd
/etc/openblade/secrets.env          # 0600, root-owned
/var/lib/openblade/openblade.db     # SQLite catalog (never in overlay, never NFS)
/var/cache/openblade/               # hydration cache
/var/spool/openblade/staging/       # restore staging
/srv/openblade/restore/             # restore destination
/usr/share/openblade/ui/            # built React frontend
```

### ExecStartPre hardware preflight
Before service becomes ready, verify:
1. `lsscsi -g` finds expected devices
2. `mtx status` succeeds
3. Read-only inventory passes

---

## 4. Configuration & Safety Gate

```ini
# /etc/openblade/openblade.env
OPENBLADE_BACKEND=real
OPENBLADE_REAL_HARDWARE_ENABLED=true
OPENBLADE_DB_URL=sqlite:////var/lib/openblade/openblade.db
OPENBLADE_STAGING_DIR=/var/spool/openblade/staging
OPENBLADE_LOG_LEVEL=INFO
```

**Safety gate is non-negotiable:** Default install stays in mock mode. Real mode is an explicit operator commissioning step.

---

## 5. Package Distribution

**Primary artifact: Debian/RPM package** built with `nfpm` or `fpm`.

Package must:
- Create system user `openblade`
- Install systemd unit
- Create `/var/lib/openblade`, `/var/cache/openblade`, `/var/spool/openblade`
- Declare host dependencies: `mtx`, `lsscsi`, `sg3-utils`, `ltfs`, `fuse3`

**Artifact pipeline:**
1. Python wheel (`hatchling`)
2. Frontend bundle (`npm ci && npm run build`) → `/usr/share/openblade/ui/`
3. Deb/RPM via `nfpm`

---

## 6. Upgrade Path

Never lose the catalog. Protocol:
```bash
systemctl stop openblade
sqlite3 /var/lib/openblade/openblade.db ".backup '/var/lib/openblade/backups/openblade-$(date +%F-%H%M%S).db'"
apt install ./openblade_X.Y.Z_amd64.deb
alembic upgrade head
systemctl start openblade
curl http://localhost:8000/health
```

**Action required:** Migrate from `Base.metadata.create_all()` to **Alembic migrations** before GA.

---

## 7. Observability

### Prometheus metrics (`/metrics`)
```
openblade_drive_online{drive_id="0"}
openblade_changer_online
openblade_inventory_last_success_timestamp
openblade_job_queue_depth
openblade_job_duration_seconds
openblade_mount_failures_total
openblade_command_failures_total{command="mtx|ltfs|lsscsi"}
```

### Alerts
- `changer_online == 0 for 5m`
- `drive_online == 0 for 5m`
- `time() - inventory_last_success_timestamp > 600`
- Job queue stuck (configurable threshold)

### Logging
- structlog, JSON only in prod
- Include: `request_id`, `job_id`, `barcode`, `drive_id`, `command`, `rc`, `elapsed_seconds`

---

## 8. Security (LAN Appliance)

Bind uvicorn to localhost, put Caddy in front:
```
tls internal
basicauth {
  operator $HASHED_PASSWORD
}
reverse_proxy 127.0.0.1:8000
```

Minimum: **HTTPS + Basic Auth + host firewall to management VLAN**  
Preferred remote access: **WireGuard or Tailscale** (not direct internet exposure)

---

## 9. High Availability

**v1: single-active only.** Do not run two nodes against one library.

If HA is needed later: active/passive with `keepalived` floating IP + `Pacemaker/Corosync` fencing. Full multi-node requires migrating off SQLite to etcd/Consul/Postgres advisory locks.

---

## 10. CI/CD

GitHub Actions pipeline:
1. `ruff check` / `ruff format --check`
2. `mypy`
3. `pytest -m "not real_hardware"`
4. Frontend build (`npm ci && npm run build`)
5. Package build (wheel + deb/rpm)

**Real hardware in CI:** Self-hosted runner with labels `[self-hosted, linux, tape, lto8]`.  
Serialize with `concurrency.group: tape-library-lab1, cancel-in-progress: false`.  
Trigger: `workflow_dispatch`, nightly, protected branch push.

---

## 11. UX Design Decisions

### Dashboard — above the fold answers 4 questions:
1. Is the appliance healthy?
2. What's running right now?
3. Do I need to do anything?
4. How much capacity / drive availability remains?

### Health states
- **Healthy** — green, all drives and media operating normally
- **Degraded** — amber, operational with warnings
- **Critical** — red, jobs blocked or hardware unavailable

### Sharding UX — goal-oriented profiles (not technical labels)
- "Standard" (single tape)
- "Parallel Throughput" (STRIPE — files across tapes)
- "Large File Archive" (BLOCK_STRIPE — file split across tapes)
- Advanced mode (collapsed, for experts)

### Job UX — two-level progress
1. Phase tracker: Preparing → Mounting → Writing → Verifying → Finalizing → Done
2. Quantitative: bytes/total, throughput MB/s, ETA

### Poll intervals
- Dashboard summary: 10–15s
- Job detail (active job): 2–5s
- Unfocused tab: 15–30s
- Never global 1s polling

### The 3 UX mistakes tape software always makes (and how OpenBlade avoids them)
1. **Treating tape like disk** — OpenBlade embraces phases, mounts, queues, media limits
2. **Showing hardware state without operator meaning** — translates device events to impact + action
3. **Exposing storage-engine internals as everyday choices** — policy-driven defaults, progressive disclosure

---

## 12. Hardware Integration Decisions

### Device identity
Use `lsscsi -g` for discovery → resolve to `/dev/tape/by-id/*` stable symlinks → create udev rules for `/dev/openblade/changer-main`, `/dev/openblade/drive-00`, etc.

### Changer reliability rule
Every move: `mtx status` → execute → `mtx status` → reconcile. Retry transient failures max 2× (2s, 10s backoff). Any ambiguous post-op state → DEGRADED, block further robotics.

### LTFS dirty mount = first-class state
Any of: failed write, failed unmount, SIGKILL of LTFS, host reboot while mounted → tape state = `DIRTY` → robotics blocked → explicit recovery workflow required.

### BLOCK_STRIPE production concern
**Hardware verdict: do not ship pure BLOCK_STRIPE without redundancy metadata.**  
Missing one shard = unrecoverable object. Ship a shard manifest on every lane + catalog. Market BLOCK_STRIPE as a throughput mode, not a preservation mode.

### 5 hardware failure scenarios that will definitely happen in production

| Scenario | Recovery |
|---|---|
| Host reboots during LTFS write | Mark DIRTY → block unload → RO mount → validate → operator approves RW recovery → re-verify |
| `mtx load` times out but tape moved | Run `mtx status` twice → reconcile → if inconsistent, freeze + operator inspection |
| Drive fails during 4-lane BLOCK_STRIPE | Mark shard group INCOMPLETE → do not catalog as archived → replace drive → restart whole write |
| Cartridge jams on unload | Stop all robotics → collect sense data → vendor-approved manual extraction → quarantine |
| Rising recovered write/read errors | Remove drive from scheduler → cleaning cycle → sg_logs baseline → test with sacrificial tape → requalify after service |

---

## 13. Open Source Strategy

### Documentation: MkDocs Material
- `/docs/quickstart/`
- `/docs/architecture/`
- `/docs/operator-guide/`
- `/docs/runbooks/`
- `/docs/api/`

### Community
- GitHub Discussions (searchable knowledge, roadmap)
- Discord (live troubleshooting, office hours)
- GitHub Issues (bugs only)

### First users: target via
- r/homelab, r/DataHoarder
- Hacker News "Show HN"
- STH forums
- Digital preservation / archives communities

### Versioning
- SemVer, currently `0.x` phase
- ship `1.0.0` only when operator flows stabilize
- `CHANGELOG.md` using Keep a Changelog format
- Stability matrix: Mock=stable, API=beta, Real hardware inventory=beta, Real write workflows=experimental

### License
Keep MIT. Add disclaimer:  
*"OpenBlade is an independent project and is not affiliated with or endorsed by Quantum."*  
Use DCO (not CLA) for contributor sign-off.

---

## Prioritized Ship List (Consensus across all 4 experts)

| Priority | What | Tools |
|---|---|---|
| 1 | Bare-metal Deb/RPM packaging | `nfpm`, systemd unit, host deps |
| 2 | Alembic migrations + DB to `/var/lib` | `alembic`, `sqlite3 .backup` |
| 3 | Production runtime config + hardware preflight | systemd EnvironmentFile, ExecStartPre |
| 4 | Observability: metrics + JSON logs + alerts | `prometheus_client`, `structlog`, Alertmanager |
| 5 | Secure appliance edge | Caddy reverse proxy, TLS, Basic Auth, UFW |
| 6 | React dashboard (v1.1) | React 18 + Vite + Tailwind dark mode |
| 7 | Real hardware CI runner | Self-hosted GitHub Actions, `concurrency` lock |
| 8 | MkDocs operator manual + runbooks | MkDocs Material |
| 9 | Community launch | GitHub Discussions, Discord, Show HN post |
| 10 | Commercial: support contracts + HW bundles | After proven adoption |

---

## The One-Paragraph Pitch

> OpenBlade is an open-source tape library control plane for Linux admins who want LTFS archive workflows without buying a proprietary controller blade or a full enterprise backup suite. It gives you safe inventory, formatting, archive, restore, verification, and cataloging through a CLI and API, and — crucially — you can test the whole thing in a simulator before pointing it at real hardware. If you already own tape gear and want something modern, scriptable, and affordable, OpenBlade is the easiest way to see whether an open tape appliance can replace a five-figure vendor box.
