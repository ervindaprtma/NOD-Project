# NOD — Network Observability Dashboard

**Enterprise-grade FortiGate network observability platform for NOC teams.**

---

## Architecture

```
Browser → Nginx (HTTPS:443) → Frontend (Next.js:3000)
                             → Backend (FastAPI:8000) → PostgreSQL
                                                     → OpenSearch (DC/DRC)
```

---

## Deploy

### Prerequisites

- Docker Engine ≥ 24.x
- Docker Compose v2
- Network access to OpenSearch clusters (DC: opensearch-dc.internal, DRC: opensearch-drc.internal)
- Port 80/443 available on host

### First-Time Setup

```bash
# 1. Clone repository
git clone https://github.com/ervindaprtma/NOD-Project.git
cd NOD-Project

# 2. Configure environment
cp .env.example .env
# Edit .env — set JWT_SECRET, POSTGRES_PASSWORD, OpenSearch endpoints

# 3. Generate SSL certificate (self-signed for dev)
mkdir -p nginx/certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/certs/nod-selfsigned.key \
  -out nginx/certs/nod-selfsigned.crt \
  -subj "/CN=localhost"

# 4. Build and start all services
docker compose up -d --build

# 5. Wait for healthy status
#    (the backend runs `alembic upgrade head` itself on start — see Database Migration)
docker compose ps

# 6. Create superadmin account (first time only, interactive)
docker compose exec backend python scripts/seed_superadmin.py
```

### Deploying Updates (existing server)

The backend source is **baked into its image** and runs `alembic upgrade head` itself on
start, so shipping a new version is one command from the repo root:

```bash
git pull
docker compose up -d --build      # rebuild changed images, recreate containers, run pending migrations
docker compose ps                 # wait for all services 'healthy'
```

- Code changes (backend or frontend) require `--build` — the source is baked, not bind-mounted.
- **Roll back:** `git checkout <previous-tag-or-commit>` then re-run the same command.
- Only `.env`, `nginx/`, and `nginx/certs/` are host-local; everything else travels with
  `git clone` (see [Migrate to a New VM](#migrate-to-a-new-vm--host-keep-credentials--sessions--logs)).

### Environment Variables (.env)

**Required:**

| Variable | Description | Example |
|----------|-------------|---------|
| `JWT_SECRET` | JWT signing key (≥32 chars) | `openssl rand -base64 32` |
| `POSTGRES_PASSWORD` | Database password | `your_password_here` |
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://nod_user:pass@db:5432/nod_db` |
| `OPENSEARCH_DC_URL` | DC OpenSearch (Site_FGT-DC) | `https://opensearch-dc.internal:9200` |
| `OPENSEARCH_DRC_URL` | DRC OpenSearch (Site_FGT-DRC + Site_FGT_Office) | `https://opensearch-drc.internal:9200` |
| `OPENSEARCH_IPSEC_URL` | IPsec OpenSearch | `https://opensearch-drc.internal:9200` |

**Optional (Notifications):**

| Variable | Description |
|----------|-------------|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | Email alerts |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts |
| `DISCORD_WEBHOOK_URL` | Discord alerts |

**Optional (System Logs — admin "System Logs" console):**

All have safe defaults — nothing needs to be set for the feature to work. The
`system_logs` table is created automatically by the startup migration. Set these
only to tune retention/volume. Credentials are never stored (redacted on write);
`username` always is.

| Variable | Default | Description |
|----------|---------|-------------|
| `SYSTEM_LOG_ENABLED` | `true` | Master switch for the queryable DB log sink. `false` disables it (file/stdout logs keep running). |
| `SYSTEM_LOG_CAPTURE_INFO_REQUESTS` | `true` | Persist every successful API call as INFO. Set `false` to cut DB volume the most. |
| `SYSTEM_LOG_INFO_RETENTION_DAYS` | `7` | Days to keep INFO rows (highest volume — lower to save disk). |
| `SYSTEM_LOG_RETENTION_DAYS` | `30` | Days to keep WARNING rows. |
| `SYSTEM_LOG_AUDIT_RETENTION_DAYS` | `90` | Days to keep ALERT + ERROR rows (audit/failure trail). |
| `SYSTEM_LOG_QUEUE_MAX` | `10000` | In-memory ring buffer size; drops oldest on overflow (counted as `dropped_rows`). |
| `SYSTEM_LOG_FLUSH_SECONDS` | `2.0` | Batch-write cadence to Postgres. |
| `SYSTEM_LOG_FLUSH_MAX_ROWS` | `500` | Batch-size hint for the writer. |

Retention is pruned daily by a scheduled job. Only INFO grows fast; if disk is a
concern on a busy site, set `SYSTEM_LOG_INFO_RETENTION_DAYS=3` or
`SYSTEM_LOG_CAPTURE_INFO_REQUESTS=false`.

#### Optional (Alert engine)

All optional; defaults are sensible. Tune only to change how fast rules fire/resolve/re-notify.

| Variable | Default | Description |
|----------|---------|-------------|
| `ALERT_POLL_INTERVAL_SECONDS` | `60` | Scheduler tick — how often every rule is evaluated. |
| `ALERT_RENOTIFY_INTERVAL_MINUTES` | `30` | While FIRING, re-send a reminder this often (per-rule override wins; `0` = notify once). |

**Resolve hysteresis (anti-flap)** is not an env var — it mirrors each rule's **Sustain**
(`sustained_for_minutes`, set per-rule in the Alert Rules menu). A rule that must breach for
N minutes to FIRE must also read clear for N minutes to RESOLVE (symmetric fire-slow/resolve-slow),
so bursty metrics (per-app AppID Scan speed) can't flap fire↔resolve. `Sustain = 0` → resolve on
the first clear tick.

### Access

| URL | Description |
|-----|-------------|
| `https://your-domain.example/` | Dashboard (auto-redirect to login) |
| `https://your-domain.example/login` | Login page |
| `https://your-domain.example/api/docs` | Swagger UI |
| `https://your-domain.example/health` | Health check |

---

## Features by Page

Monitoring spans three sites (**DC**, **DRC**, **Office**) sourced from FortiGate flow/metric
data in OpenSearch. Every page shares a common shell: a **time-range picker** (15m/30m/1h/2h/4h
presets + a Custom datetime drawer) and an **auto-refresh** selector (Off/15s/30s/60s). Traffic,
SD-WAN, and Resources charts support **drag-to-zoom** (brush a region, then "Reset zoom").
Chart primitives are area/line charts, hand-rolled SVG stacked bars, d3-sankey diagrams,
ranked-bar/KPI cards, and HTML tables. Min role in the table is the nav/server-enforced floor
(see [Roles & Access](#roles--access-rbac)).

| Page | What it shows | Visual | Min role |
|------|---------------|--------|----------|
| **Overview** | NOC landing page — KPIs, device health, SD-WAN link state, site availability, WAN/MPLS bandwidth, top talkers across all sites | Clickable card grid: 5 KPI cards (SSL/IPsec users, device count, HA, active Alerts), per-site Device Health (CPU/mem gauges, sessions, HA pills), SD-WAN Link Status, Site Availability (own 24h/7d/30d toggle), WAN/MPLS bandwidth, ranked top-talker rows — every card deep-links to detail | viewer |
| **Traffic Internet** | Per-site outbound internet analytics — top apps/categories/AS/countries/clients/servers, interface & protocol mix | **Overview** + **Sankey Diagram** tabs; multi-field filter drawer (app, IP, protocol, port, interface, include/exclude chips). Overview: ranked cards + Total/App throughput charts + flow-records table. Sankey: Upload & Download d3-sankey | viewer |
| **Traffic Inbound** | Per-site inbound VIP traffic to published services — top services, client AS/countries, IPs, interfaces | Same shell as Traffic Internet — **Overview** + **Sankey** tabs, ranked cards, throughput charts, flow table | viewer |
| **Traffic Internal** | East-west (LAN) traffic — intra-LAN & inter-site flows | **Overview** + **Sankey** tabs, plus a Traffic-Path selector (All / Intra-LAN / Inter-Site); ranked cards, throughput charts, flow table | viewer |
| **SD-WAN SLA** | Per-link SLA — status + latency/jitter/packet-loss time-series with per-link summary KPIs | Single view; link filter bar (WAN/MPLS/individual). Link Status table, SLA Summary KPI cards, and Latency/Jitter/Loss area charts (each with "View Full" expand) | viewer |
| **Resources** | FortiGate device health — CPU/mem/sessions, HA cluster, interface bandwidth, uptime SLA | **Resource Usage** + **Interface Bandwidth** + **Availability** tabs. Usage: HA panel + per-device timeline charts. Bandwidth: per-interface In/Out cards. Availability: own SLA window, SLA table, reboot history, dual charts | viewer |
| **VPN Sessions** | SSL & IPsec VPN — active users per protocol + reconstructed session history | SSL VPN table + IPsec VPN table (active-count badges, "View Full"), and a Session History table with client-side filters (user/type/device) | viewer |
| **Raw Data** | Raw OpenSearch flow-record browser — paginated, filterable, exportable | Data table; site + traffic-path selectors, column-visibility & page-size controls, filter drawer, CSV export, cursor pagination (cap 10k) | **operator** |
| **Alerts** | Alert rule engine management + firing/resolved history (live SSE indicator) | **Alert Rules** + **Alert History** tabs. Rules: template gallery, engine-health line, rules table (toggle/test/edit/delete), Create/Edit modal. History: searchable event table with per-clause fire/resolve snapshots | view: viewer · rules: **admin** |
| **Reports** | On-demand report generation (10 types) & distribution | Generate form (type picker R-01…R-10, site checkboxes, PDF/HTML/DOCX, range, type-specific options) + Report History table (download / preview / distribute modal) | **operator** |
| **Users** | User CRUD + active-session administration | Users table with role badges + Create/Edit/Delete modal; Active Sessions panel | **superadmin** |
| **Activity Logs** | User-action audit trail (logins, user/rule CRUD, report actions) | Single table (time, action, user, role, source IP, details JSON) with action filter; auto-refresh 30s | **superadmin** |
| **System Logs** | Backend+frontend log console (INFO/WARNING/ERROR/ALERT) | Log table; multi-select level filters with facet counts, source/category/time/user/search filters, inline row expansion, CSV export, live refresh | **admin** |
| **Settings** | Per-user preferences + admin messaging/maintenance config | Tabs: Change Password, Profile, Appearance (theme); **admin-only** tabs: Notification Channels, Message Templates, Maintenance Windows | viewer · config: **admin** |

---

## Alerting

A background engine evaluates every enabled rule on a **60-second tick** through a state machine:
`INACTIVE → PENDING → FIRING → RESOLVED`. A rule's **Sustain** debounces firing (breach must hold
N minutes) and now symmetrically debounces resolving (clear must hold the same N minutes) so bursty
metrics can't flap. Reads that hit degraded OpenSearch are **held**, not falsely resolved.

- **Rule kinds:** *single-metric* (one data source + metric + aggregation + condition + threshold)
  and *composite* (multiple clauses combined with **any** = OR / **all** = AND logic).
- **Data sources:** `appid_flow` (application traffic scan), `sdwan_sla` (latency / jitter / packet
  loss / link status), `interface_stats` (bandwidth / utilization / oper-status), `device_uptime`,
  `ha_resource` (CPU / memory / sessions), `vpn_ssl`, `vpn_ipsec`.
- **Severity:** INFO · WARNING · CRITICAL. **Aggregations:** avg / max / min / sum / count.
- **Notifications:** Telegram and Email, per-rule channels, with a configurable re-notify cadence
  (global default, per-rule override, or notify-once). Redacted — credentials are never sent.
- **History:** every fire and resolve is recorded with a stable **event code**, full per-metric
  *was → now* detail, and the exact message payload sent, searchable from the Alert History tab
  (readable by all roles). Rule config lives with admins; see below.

---

## Roles & Access (RBAC)

Four roles form a strict hierarchy — each inherits everything below it:

**`viewer` (0) → `operator` (1) → `admin` (2) → `superadmin` (3)**

Enforcement is **server-side** on every endpoint via `require_role(...)` (authoritative); the UI
additionally hides nav items and gates in-page controls below your role (`hasMinRole`).

| Role | Can do |
|------|--------|
| **viewer** | Read all monitoring — Overview, Traffic (Internet/Inbound/Internal), SD-WAN SLA, Resources, VPN Sessions; view Alerts + Alert History; own profile, password & theme in Settings |
| **operator** | *viewer* **+** browse **Raw Data**, generate/download/distribute **Reports**, and instantiate alert rules from templates |
| **admin** | *operator* **+** full **Alert** rule & test management, **notification channels / message templates / maintenance windows**, and the **System Logs** console |
| **superadmin** | *admin* **+** **User** management (create/edit/delete, active sessions) and the **Activity Logs** audit trail |

Notable in-page gates: the **Alerts → Rules** tab is admin+ (viewers land on **History**); **Settings**
notification/template/maintenance tabs are admin-only; **Users** delete is superadmin-only and cannot
target yourself; **Activity Logs** and **System Logs** return a role-gated "Access Denied" block below
their required role.

---

## Production Deployment (Nginx + Domain)

### Nginx Reverse Proxy

The nginx container terminates SSL and proxies to frontend/backend:

```nginx
upstream frontend_upstream { server frontend:3000; }
upstream backend_upstream { server backend:8000; }

server {
    listen 80;
    server_name your-domain.example;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.example;

    ssl_certificate /etc/nginx/certs/nod-selfsigned.crt;
    ssl_certificate_key /etc/nginx/certs/nod-selfsigned.key;

    # Proxy to frontend
    location / {
        proxy_pass http://frontend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy to backend API
    location /api/ {
        proxy_pass http://backend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Proxy to backend auth endpoints
    location /auth/ {
        proxy_pass http://backend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### SSL Certificate (Production)

```bash
# Option 1: Self-signed (dev/testing)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/certs/nod-selfsigned.key \
  -out nginx/certs/nod-selfsigned.crt \
  -subj "/CN=your-domain.example"

# Option 2: Let's Encrypt (production)
certbot certonly --webroot -w /usr/share/nginx/html -d your-domain.example
# Update nginx.conf with certificate paths, then restart nginx
docker compose restart nginx
```

### Domain Configuration

1. Create DNS A record: `your-domain.example` → Nginx server IP
2. Ensure Nginx server has ports 80/443 open
3. Update `.env` with production values:
   ```
   NEXT_PUBLIC_API_BASE_URL=https://your-domain.example
   ALLOWED_ORIGINS=https://your-domain.example
   ```

---

## Development

CI (`.github/workflows/code-quality.yml`, on every PR) runs exactly these — run them
before pushing:

```bash
# Backend
ruff check backend/
pip-audit -r backend/requirements.txt
cd backend && pytest tests/ -q          # pytest.ini puts backend/ on sys.path
cd backend && alembic check             # schema matches models? See Database Migration

# Frontend
cd frontend && npx tsc --noEmit && npm audit --audit-level=high
```

Note: `mypy` passes in CI without the app's dependencies installed, which makes
`ignore_missing_imports` treat sqlalchemy/jose/jinja2 as `Any` — so `warn_return_any`
fires on passthrough returns that are fine locally. Annotate the local rather than
returning the call directly:

```python
user: User | None = result.scalar_one_or_none()
return user
```

---

## Security Features

| Feature | Implementation |
|---------|----------------|
| Rate Limiting | slowapi — 10 req/min on login, 30 req/min on refresh |
| SSE Auth | EventSource can't set headers, so `POST /stream-token` (Bearer-authed, admin+) issues a 5-min token for `?token=` |
| Refresh Rotation | New JWT pair on every /auth/refresh call |
| CSRF Protection | `__Host-` cookie prefix + SameSite=Strict |
| JWT Secret | ≥32 chars enforced at startup |
| Role Guard | Alert stream restricted to admin+ |

---

## Maintenance

### Common Commands

```bash
# Check service status
docker compose ps

# Restart a specific service
docker compose restart backend
docker compose restart frontend

# Rebuild after code changes
docker compose build backend
docker compose build frontend
docker compose up -d

# Full rebuild (no cache)
docker compose build --no-cache
docker compose up -d

# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes database)
docker compose down -v
```

### Database Migration

Migrations run automatically: the backend's `CMD` is `alembic upgrade head && uvicorn …`,
so every `docker compose up` applies pending migrations before serving. A failed
migration stops the container rather than serving a broken schema — don't loosen that
`&&`. The commands below are for authoring and inspection.

```bash
# Check current migration version
docker compose exec backend alembic current

# Create new migration (after model changes)
docker compose exec backend alembic revision --autogenerate -m "description"

# Does the schema match the models? (exits non-zero on drift)
docker compose exec backend alembic check
```

**Always run `alembic check` after changing a model.** `upgrade head` only applies
migrations that exist — it cannot fix a column you declared but never wrote a migration
for. That gap ("head" reported while the table lacks the column) has caused five runtime
outages in this repo, so CI now gates on `alembic upgrade head && alembic check` against
a scratch Postgres.

Review autogenerated migrations before applying — two recurring traps:

- A new `NOT NULL` column needs a `server_default`, or it fails on any site whose table
  already has rows (DC may be empty while DRC isn't).
- `create_foreign_key(None, …)` leaves the constraint unnamed, and the generated
  `drop_constraint(None, …)` in `downgrade()` cannot run. Name it.

### Seed Superadmin

```bash
# Create initial superadmin account (interactive — prompts for username/password)
docker compose exec backend python scripts/seed_superadmin.py
```

### Backup & Restore

```bash
# Backup database
docker compose exec db pg_dump -U nod_user nod_db > backup_$(date +%Y%m%d).sql

# Restore database
cat backup_20260620.sql | docker compose exec -T db psql -U nod_user nod_db
```

---

### Migrate to a New VM / Host (keep credentials + sessions + logs)

Moves all state — user password hashes, active sessions, activity logs, and
encrypted notification secrets — to a new host with **no credential loss and no
forced re-login** (when the domain stays the same).

**Where the state lives:**

| State | Location |
|---|---|
| User passwords | Postgres `users.hashed_password` — **bcrypt** (portable, no plaintext) |
| Sessions | `refresh_tokens` table + client `__Host-nod_refresh_token` cookie (access tokens are stateless JWT, not stored) |
| Activity / session logs | `user_activity_logs` table |
| Notification secrets | `notification_configs` — **Fernet-encrypted with a key derived from `JWT_SECRET`** |
| All DB data | Docker volume `network_project_postgres_data` |
| Secrets | `.env` (gitignored) — `JWT_SECRET`, `POSTGRES_PASSWORD`, `OPENSEARCH_*`, notifier tokens |
| TLS + proxy | `nginx/certs/*`, `nginx/nginx.conf` (tracked in git) |

> ⚠️ **`JWT_SECRET` MUST be carried over verbatim.** It signs every session token
> *and* derives the key that encrypts `notification_configs`. Change it and you
> invalidate all sessions **and** permanently corrupt the stored notification
> secrets. `POSTGRES_PASSWORD` must also match the migrated data.

**On the OLD host:**

```bash
cd /path/to/network_project
# Full logical dump (custom format) — users+hashes, refresh_tokens, activity logs,
# notification_configs, and alembic_version all travel together.
docker compose exec -T db pg_dump -U nod_user -d nod_db -Fc > nod_db.dump

# Bundle the secrets git will NOT carry (certs + nginx.conf come with `git clone`).
tar czf nod_secrets.tgz .env
```

Copy `nod_db.dump` and `nod_secrets.tgz` to the new host (scp/rsync).

**On the NEW host:**

```bash
git clone https://github.com/ervindaprtma/NOD-Project.git network_project
cd network_project
tar xzf /path/nod_secrets.tgz            # restores .env verbatim (JWT_SECRET intact)

docker compose up -d db                  # inits an empty volume using POSTGRES_PASSWORD from .env
# wait until db is healthy, then restore INTO the fresh db (before backend/alembic runs):
docker compose exec -T db pg_restore -U nod_user -d nod_db --clean --if-exists < nod_db.dump

docker compose up -d --build             # backend's `alembic upgrade head` is a no-op (dump is already at head)
```

Verify: `curl -sk https://localhost/health` → all `ok`, and existing users log in
with their **existing** passwords.

**Alternative — byte-exact volume copy** (only if the new host runs the *same*
Postgres major version, 15). `pg_dump` above is version-safe and preferred.

```bash
# OLD (stack stopped): tar the volume
docker compose down
docker run --rm -v network_project_postgres_data:/d -v "$PWD":/b alpine tar czf /b/pgdata.tgz -C /d .
# NEW: recreate the volume from the tarball, then `docker compose up -d --build`
docker volume create network_project_postgres_data
docker run --rm -v network_project_postgres_data:/d -v "$PWD":/b alpine sh -c 'cd /d && tar xzf /b/pgdata.tgz'
```

**Notes**
- Same domain (`your-domain.example`) **+** same `JWT_SECRET` → active sessions survive
  (the refresh cookie still validates). Different domain → users simply re-login;
  no stored data is lost either way.
- Point DNS at the new host and confirm it can reach the OpenSearch clusters
  (`opensearch-dc.internal`, `opensearch-drc.internal`) before going live.

---

## Check Logs

### View Logs

```bash
# All services (live tail)
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f nginx
docker compose logs -f db

# Last 100 lines
docker compose logs --tail 100 backend
```

### Application Logs (inside container)

Backend writes structured JSON logs to `/app/logs/`:

```bash
# Access log (all API requests)
docker compose exec backend cat logs/access.log

# Error log (warnings + errors)
docker compose exec backend cat logs/error.log

# Tail access log
docker compose exec backend tail -f logs/access.log

# Search for errors
docker compose exec backend grep '"level":"ERROR"' logs/error.log
```

### Docker Health Status

```bash
# Check if all services are healthy
docker compose ps

# Backend health endpoint
curl -k https://your-domain.example/health

# Expected response:
# {"api":"ok","db":"ok","opensearch_dc":"ok","opensearch_drc":"ok","opensearch_ipsec":"ok"}
```

---

**License:** Internal — Confidential
**Contact:** NOC Engineering Team
**Repository:** https://github.com/ervindaprtma/NOD-Project
