# NOD Backend — Comprehensive Design Report

**Project:** NOD (Network Observability Dashboard)  
**Version:** 1.0.0  
**Runtime:** Python 3.11 / FastAPI / Uvicorn (ASGI)  
**Date:** 2025-07-14  

---

## Table of Contents

1. [Project Overview & Architecture](#1-project-overview--architecture)
2. [Infrastructure](#2-infrastructure)
3. [Core Module](#3-core-module)
4. [Database Layer](#4-database-layer)
5. [API Layer — 17 Route Modules](#5-api-layer--17-route-modules)
6. [OpenSearch Query Builders — 10 Modules](#6-opensearch-query-builders--10-modules)
7. [Services Layer](#7-services-layer)
8. [Schema System (Pydantic Models)](#8-schema-system-pydantic-models)
9. [Data Flow Architecture](#9-data-flow-architecture)
10. [Known Issues & Technical Debt](#10-known-issues--technical-debt)
11. [File Inventory](#11-file-inventory)

---

## 1. Project Overview & Architecture

### 1.1 Purpose

NOD is a **Network Observability Dashboard** that provides real-time visibility into FortiGate firewall infrastructure across three sites:

- **Site_FGT-DC** — Data Center (HA cluster, primary site)
- **Site_FGT-DRC** — Disaster Recovery Center
- **Site_FGT_Office** — Office site

The backend aggregates data from **OpenSearch** (Elasticsearch-compatible) time-series clusters and **PostgreSQL** (relational) to serve a React/Next.js frontend.

### 1.2 High-Level Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   Backend    │────▶│  OpenSearch   │
│  (Next.js)   │◀────│  (FastAPI)   │◀────│  (3 clusters) │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                     ┌──────┴───────┐
                     │  PostgreSQL   │
                     │  (asyncpg)    │
                     └──────────────┘
```

### 1.3 Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| ASGI Server | Uvicorn | HTTP/WS server |
| Web Framework | FastAPI | REST + WebSocket APIs |
| ORM | SQLAlchemy (async) | PostgreSQL access |
| Migrations | Alembic (async) | Schema management |
| Search/Analytics | OpenSearch (opensearch-py async) | Time-series queries |
| Auth | python-jose + passlib/bcrypt | JWT + password hashing |
| Background Tasks | APScheduler | Alert engine + report scheduling |
| Report Generation | WeasyPrint + python-docx + Jinja2 | PDF/HTML/DOCX reports |
| Charts | Matplotlib (Agg backend) | Server-side PNG charts |
| Notifications | aiosmtplib + httpx | Email + Telegram/Discord/WhatsApp |
| Config | pydantic-settings | Environment variable validation |
| Logging | python-json-logger | Structured JSON logs |

### 1.4 Key Architectural Patterns

1. **Async Everything** — All database, OpenSearch, and HTTP operations are fully async.
2. **APIResponse Envelope** — Every endpoint returns `{"success": bool, "data": ..., "meta": ..., "error": ...}`.
3. **RBAC** — 4-role hierarchy: `viewer < operator < admin < superadmin`.
4. **OpenSearch Compliance Mandates** (Q-01 through Q-08):
   - Q-01: All queries include `@timestamp` range filter (`gte`/`lte`)
   - Q-02: Terms aggregations use explicit `size`
   - Q-03: `_source` filtering — never `_source: true`
   - Q-04: `search_after` pagination (no scroll)
   - Q-05: `date_histogram` with `fixed_interval`
   - Q-06: Exact `measurement_name.keyword` term filter per site
   - Q-07: No N+1 patterns — use composite/terms aggregations or batch fetches
   - Q-08: Page size capped at 10,000 (500 for raw data)
5. **Fire-and-Forget Logging** — Activity logging never blocks or crashes the request.

### 1.5 Multi-Site Data Routing

| Site | OpenSearch Cluster | Flow Index | Telegraf Index |
|------|-------------------|------------|----------------|
| Site_FGT-DC | DC (10.80.150.108) | fortigate-appid-flow-* | telegraf-index* |
| Site_FGT-DRC | DRC (10.90.150.108) | fortigate-appid-flow-* | telegraf-index* |
| Site_FGT_Office | DRC (10.90.150.108) | fortigate-appid-flow-* | telegraf-index* |
| IPsec VPN | IPsec (10.90.150.108) | ipsec-* | — |

---

## 2. Infrastructure

### 2.1 Dockerfile (35 lines)

**File:** `backend/Dockerfile`

```
Base Image:    python:3.11-slim
System Deps:   pango, gdk-pixbuf, cairo, libffi, libglib (WeasyPrint), curl
Package Mgmt:  pip install -r requirements.txt
Exposed Port:  8000
CMD:           uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

The `--proxy-headers` flag enables correct client IP resolution behind a reverse proxy (Nginx).

### 2.2 requirements.txt (48 lines)

**File:** `backend/requirements.txt`

| Category | Packages |
|----------|----------|
| ASGI Framework | `fastapi`, `uvicorn[standard]` |
| Database | `sqlalchemy[asyncio]`, `asyncpg`, `alembic` |
| OpenSearch | `opensearch-py[async]` |
| Auth | `python-jose[cryptography]`, `passlib[bcrypt]`, `bcrypt` |
| Background Tasks | `apscheduler` |
| HTTP Client | `httpx` |
| Report Generation | `weasyprint`, `python-docx`, `jinja2` |
| Charts | `matplotlib` |
| Email | `aiosmtplib` |
| Config | `pydantic-settings`, `pydantic[email]` |
| Logging | `python-json-logger` |

### 2.3 Alembic Migrations

**Files:** `backend/alembic/env.py` (66 lines), `backend/alembic/versions/08bcffb3a374_initial_schema.py` (196 lines)

- Uses **async engine** (`run_async_migrations` pattern with `asyncio.run`)
- Single migration creates **10 tables**:
  1. `users` — User accounts
  2. `refresh_tokens` — JWT refresh token persistence
  3. `alert_rules` — Alert rule definitions
  4. `alert_templates` — Alert message templates
  5. `alert_logs` — Alert firing history
  6. `alert_states` — Current alert evaluation state per rule
  7. `user_activity_logs` — Audit trail
  8. `notifications` — User notification inbox
  9. `report_jobs` — Async report generation jobs
  10. `report_schedules` — Scheduled report configurations

**Note:** `user_preferences` and `user_pinned_widgets` tables are defined in `models.py` but NOT in the initial migration.

### 2.4 Seed Script

**File:** `backend/scripts/seed_superadmin.py` (144 lines)

Creates the initial superadmin user with configurable credentials. Used during first-time deployment.

---

## 3. Core Module

### 3.1 Configuration — `app/core/config.py` (107 lines)

Central configuration via `pydantic-settings`, loaded from `.env` file. Uses `@lru_cache` for singleton access.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
```

**Configuration Categories:**

| Category | Settings | Defaults |
|----------|----------|----------|
| Service Ports | `NGINX_PORT`, `FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT` | 80, 3000, 8000, 5432 |
| Frontend | `NEXT_PUBLIC_API_BASE_URL` | http://localhost:80 |
| Security | `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_HOURS`, `ALLOWED_ORIGINS` | HS256, 60min, 24h |
| Database | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL` | nod_db, nod_user |
| OpenSearch | `OPENSEARCH_DC_URL`, `OPENSEARCH_DRC_URL`, `OPENSEARCH_IPSEC_URL`, `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`, `OPENSEARCH_POOL_SIZE`, `OPENSEARCH_REQUEST_TIMEOUT` | 3 clusters, pool=10, timeout=10s |
| Site Config | `TELEGRAF_SDWAN_SITES`, `TELEGRAF_SSLVPN_SITES` | 3 SD-WAN sites, 2 SSL VPN sites |
| Alert Engine | `ALERT_POLL_INTERVAL_SECONDS`, `ALERT_RENOTIFY_INTERVAL_MINUTES` | 60s, 30min |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM_ADDRESS` | — |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — |
| WhatsApp | `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | — |
| Discord | `DISCORD_WEBHOOK_URL` | — |
| Logging | `LOG_LEVEL`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`, `SYSLOG_ENABLED/HOST/PORT` | INFO, 100MB, 10 backups |
| Timeframe | `DEFAULT_REFRESH_INTERVAL_SECONDS`, `SESSION_IDLE_TIMEOUT_HOURS` | 60s, 4h |

**Computed Properties:**
- `allowed_origins_list` — splits `ALLOWED_ORIGINS` by comma
- `sdwan_sites_list` — splits `TELEGRAF_SDWAN_SITES` by comma
- `sslvpn_sites_list` — splits `TELEGRAF_SSLVPN_SITES` by comma

### 3.2 Logging — `app/core/logging.py` (82 lines)

Structured JSON logging with three output destinations:

| Handler | Level | Target | Purpose |
|---------|-------|--------|---------|
| `access.log` | INFO only | RotatingFileHandler | Request tracing (filtered by `_AccessLogFilter`) |
| `error.log` | WARNING+ | RotatingFileHandler | Error tracking |
| stdout | All levels | StreamHandler | Docker log driver |
| syslog | All levels | SysLogHandler (optional) | External SIEM forwarding |

**Custom Formatter — `JsonFormatter`:**
Extends `pythonjsonlogger.JsonFormatter` to inject:
- `level` — log level name
- `timestamp` — formatted time
- `trace_id` — correlates with HTTP middleware trace ID

**Noisy Library Suppression:** `apscheduler`, `opensearch`, `sqlalchemy.engine`, `weasyprint` — all set to WARNING level.

### 3.3 Security — `app/core/security.py` (99 lines)

**Password Hashing:**
- Algorithm: bcrypt (via passlib)
- Cost factor: default (≥12 per NFR 7.4)
- Functions: `hash_password(password) -> str`, `verify_password(plain, hashed) -> bool`

**JWT Token Management:**

| Function | Purpose | Claims |
|----------|---------|--------|
| `create_access_token(subject, extra_claims, expires_delta)` | Short-lived API auth | `sub`, `iat`, `exp`, `jti` (UUID hex), `type=access` |
| `create_refresh_token(subject, jti, expires_delta)` | Long-lived refresh | `sub`, `iat`, `exp`, `jti`, `type=refresh` |
| `decode_token(token)` | Validates JWT | Requires `sub`, `exp`, `jti`, `type` |
| `decode_token_optional(token)` | Non-raising variant | Returns `None` on failure |

**Token Configuration:**
- Algorithm: HS256
- Access token expiry: 60 minutes (configurable)
- Refresh token expiry: 24 hours (configurable)

---

## 4. Database Layer

### 4.1 Session Management — `app/db/session.py` (44 lines)

```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,      # Detect stale connections
    pool_recycle=3600,        # Recycle connections after 1 hour
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

**FastAPI Dependency — `get_db()`:**
- Yields async session
- Auto-commits on success
- Auto-rolls back on exception
- `expire_on_commit=False` prevents lazy-load errors after commit

### 4.2 ORM Models — `app/db/models.py` (376 lines)

All models use `UUID` primary keys (stored as hex strings) and `server_default=text("now()")` for timestamps.

#### 4.2.1 User Model

```python
class User(Base):
    __tablename__ = "users"
    id: str                    # UUID(hex), PK
    username: str              # unique, indexed, max 128
    email: str                 # unique, max 255
    hashed_password: str       # bcrypt hash
    full_name: str             # max 255, default ""
    role: str                  # "superadmin|admin|operator|viewer"
    is_active: bool            # default True
    must_change_password: bool # default False
    last_login: datetime|None  # timezone-aware
    created_at: datetime       # server_default now()
    updated_at: datetime       # onupdate _utcnow()
```

**Relationships:** `refresh_tokens` (one-to-many, cascade delete), `preferences` (one-to-one, cascade delete)

#### 4.2.2 RefreshToken Model

```python
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: str              # UUID(hex), PK
    user_id: str         # FK → users.id (CASCADE), indexed
    jti: str             # unique, indexed, max 64
    expires_at: datetime
    is_revoked: bool     # default False
    created_at: datetime
```

#### 4.2.3 AlertRule Model

```python
class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: str                          # UUID(hex), PK
    name: str                        # indexed, max 255
    severity: str                    # "INFO|WARNING|CRITICAL"
    data_source: str                 # "appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec"
    metric_field: str                # e.g. "ha_member.cpu_usage"
    aggregation: str                 # "avg|max|min|sum|count"
    condition: str                   # ">|<|>=|<=|=="
    threshold_value: float
    evaluation_window_minutes: int
    sustained_for_minutes: int
    notify_channels: list[str]       # JSONB
    template_id: str|None            # FK → alert_templates.id (SET NULL)
    enabled: bool                    # default True
    created_by: str|None             # FK → users.id (SET NULL)
    created_at: datetime
    updated_at: datetime
```

**Relationship:** `template` → AlertTemplate (many-to-one)

#### 4.2.4 AlertTemplate Model

```python
class AlertTemplate(Base):
    __tablename__ = "alert_templates"
    id: str                # UUID(hex), PK
    name: str              # unique, max 255
    subject_template: str  # Text
    body_template: str     # Text
    is_default: bool       # default False
    created_at: datetime
```

**Relationship:** `rules` → AlertRule (one-to-many)

#### 4.2.5 AlertLog Model

```python
class AlertLog(Base):
    __tablename__ = "alert_logs"
    id: str                       # UUID(hex), PK
    rule_id: str                  # FK → alert_rules.id (CASCADE), indexed
    rule_name: str                # Denormalized snapshot
    severity: str                 # Denormalized snapshot
    metric_value_at_firing: float
    notified_channels: list[str]  # JSONB
    fired_at: datetime            # indexed
    resolved_at: datetime|None
    rule_snapshot: dict           # JSONB — full rule config at firing time
    created_at: datetime
```

#### 4.2.6 AlertState Model

```python
class AlertState(Base):
    __tablename__ = "alert_states"
    rule_id: str              # FK → alert_rules.id (CASCADE), PK (one state per rule)
    state: str                # "INACTIVE|PENDING|FIRING|RESOLVED"
    pending_since: datetime|None
    last_fired_at: datetime|None
    last_notified_at: datetime|None
    updated_at: datetime
```

#### 4.2.7 UserActivityLog Model

```python
class UserActivityLog(Base):
    __tablename__ = "user_activity_logs"
    id: str            # UUID(hex), PK
    user_id: str       # FK → users.id (CASCADE), indexed
    action: str        # e.g. "login", "alert_rule_created", "user_deleted"
    source_ip: str|None # max 45 (IPv6 compatible)
    details: dict|None  # JSONB
    timestamp: datetime # indexed, server_default now()
```

#### 4.2.8 ReportJob Model

```python
class ReportJob(Base):
    __tablename__ = "report_jobs"
    id: str                  # UUID(hex), PK
    report_type: str         # "R-01" through "R-08"
    output_format: str       # "pdf|html|docx"
    status: str              # "pending|running|completed|failed"
    created_by: str          # FK → users.id (SET NULL)
    time_range_start: datetime
    time_range_end: datetime
    file_path: str|None      # max 500
    file_size_bytes: int|None
    error_message: str|None  # Text
    created_at: datetime
    completed_at: datetime|None
    expires_at: datetime|None
```

#### 4.2.9 Notification Model

```python
class Notification(Base):
    __tablename__ = "notifications"
    id: str            # UUID(hex), PK
    user_id: str       # FK → users.id (CASCADE), indexed
    alert_log_id: str|None  # FK → alert_logs.id (CASCADE)
    alert_name: str
    severity: str
    message: str       # Text
    is_read: bool      # default False
    created_at: datetime
```

#### 4.2.10 ReportSchedule Model

```python
class ReportSchedule(Base):
    __tablename__ = "report_schedules"
    id: str                    # UUID(hex), PK
    report_type: str
    output_format: str         # default "html"
    cron_expression: str       # max 50
    sites: str|None            # Text (JSON array stored as text)
    sections: str|None         # Text (JSON array stored as text)
    channels: str|None         # Text (JSON array stored as text)
    recipient_email: str|None
    recipient_phone: str|None
    enabled: bool              # default True
    created_by: str            # FK → users.id (SET NULL)
    last_run_at: datetime|None
    next_run_at: datetime|None
    created_at: datetime
```

#### 4.2.11 UserPreference Model (not migrated)

```python
class UserPreference(Base):
    __tablename__ = "user_preferences"
    user_id: str              # PK, FK → users.id (CASCADE)
    theme: str                # "light|dark", default "light"
    alert_notifications_enabled: bool  # default True
    updated_at: datetime
```

#### 4.2.12 UserPinnedWidget Model (not migrated)

```python
class UserPinnedWidget(Base):
    __tablename__ = "user_pinned_widgets"
    id: str                   # UUID(hex), PK
    user_id: str              # FK → users.id (CASCADE), indexed
    widget_id: str            # e.g. "P01-A", "TF-01"
    display_order: int        # default 0
    created_at: datetime
    # UniqueConstraint("user_id", "widget_id")
```

---

## 5. API Layer — 17 Route Modules

All endpoints are registered in `app/main.py` via `app.include_router()`. The application uses the `lifespan` context manager for startup/shutdown hooks.

### 5.1 Application Entry Point — `app/main.py` (250 lines)

**Lifespan Events:**
- **Startup:** Initialize structured logging, start alert scheduler (APScheduler), start report scheduler
- **Shutdown:** Shutdown both schedulers, dispose database engine

**Middleware:**
- **CORS:** Configurable origins, credentials allowed, methods: GET/POST/PUT/PATCH/DELETE/OPTIONS, headers: Authorization/Content-Type/X-Trace-ID
- **Trace + Access Logging:** Injects `X-Trace-ID` header (UUID or client-provided), logs request method/path/status/elapsed_ms/client_ip

**Special Endpoints:**
- `GET /` — Root: returns `{"service": "NOD Backend", "version": "1.0.0"}`
- `GET /health` — Health check: verifies DB connectivity (critical) and OpenSearch clusters (non-fatal). Returns 503 if DB is unreachable.
- `WS /ws/alerts` — WebSocket for real-time alert push (FR-10). JWT-authenticated via query parameter.

### 5.2 Authentication — `app/api/auth.py` (263 lines)

**Router:** `APIRouter(prefix="/auth", tags=["Authentication"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | Public | Authenticate user, set refresh token cookie, return access token |
| POST | `/auth/logout` | Any authenticated | Revoke refresh token, clear cookie |
| POST | `/auth/refresh` | Cookie-based | Rotate refresh token, issue new access token |

**Login Flow:**
1. Validate username/password against `users` table
2. Check `is_active` flag
3. Update `last_login` timestamp
4. Fire-and-forget activity log
5. Create access token (JWT) with `sub`, `role`, `username` claims
6. Create refresh token, persist in `refresh_tokens` table
7. Set `nod_refresh_token` HTTP-only cookie (secure if HTTPS detected)

**Refresh Flow:**
1. Read refresh token from cookie
2. Decode JWT, verify type=refresh
3. Check `refresh_tokens` table (not revoked, not expired)
4. Verify user exists and is active
5. Issue new access token (does NOT rotate refresh token)

**Dependencies (used by all protected routes):**

| Dependency | Purpose |
|-----------|---------|
| `get_current_user` | Extracts JWT from `Authorization: Bearer` header, validates, returns `User` object |
| `require_role(minimum_role)` | Factory: returns dependency that enforces minimum role level |
| `get_current_user_optional` | Non-raising variant (not used in current routes) |

**RBAC Hierarchy:**
```python
_ROLE_HIERARCHY = {
    "viewer": 0,
    "operator": 1,
    "admin": 2,
    "superadmin": 3,
}
```

### 5.3 Overview Dashboard — `app/api/overview.py` (253 lines)

**Router:** `APIRouter(prefix="/api/v1", tags=["Overview"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/overview` | viewer+ | Aggregated dashboard data (FR-01) |

**Parameters:** `gte_ms` (int, required), `lte_ms` (int, required)

**Panels Aggregated (single API call):**

| Panel | Source | Description |
|-------|--------|-------------|
| P01-A | `sslvpn_qb.all_sslvpn_users_count()` | Active SSL VPN users |
| P01-B | `ipsec_qb.active_ipsec_users_count()` | Active IPsec VPN users |
| P01-C/D/E/F | `ha_qb.current_device_status()` + `ha_qb.session_sparkline()` | FortiGate device resources (CPU, memory, sessions, sync status) |
| P01-G | `appid_qb.top_applications(size=10)` | Top 10 applications |
| — | `appid_qb.top_dst_as_orgs(size=10)` | Top AS organizations |
| P01-H | `sdwan_qb.all_sites_link_status()` | SD-WAN link status for all 3 sites |
| P01-I | `appid_qb.total_throughput()` | Total throughput bytes |
| — | `ha_qb.ha_cluster_status()` | HA cluster health (DC only) |
| — | `iface_qb.interface_stats_timeline()` | WAN bandwidth per site (3 sites) |
| — | `ti_qb.flow_summary()` | Inbound VIP top services |
| — | DB query | Active alert count |

**Response:** `OverviewResponse` containing all panels + `query_took_ms` metadata.

### 5.4 Legacy Traffic Analytics — `app/api/traffic.py` (134 lines)

**Router:** `APIRouter(prefix="/api/v1/traffic", tags=["Traffic"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/traffic/summary` | viewer+ | Legacy traffic analytics (DC only via `appid_qb`) |

**Panels (10 widgets):**

| Widget | Source | Description |
|--------|--------|-------------|
| TF-01 | `appid_qb.top_applications(size=20)` | Top 20 applications |
| TF-02 | `appid_qb.application_categories()` | Application categories |
| TF-03 | `appid_qb.sankey_data(size=500)` | Sankey diagram data |
| TF-04 | `appid_qb.throughput_timeline()` | Throughput timeline |
| TF-05 | `appid_qb.top_client_ips(size=20)` | Top client IPs |
| TF-06 | `appid_qb.top_server_ips(size=20)` | Top server IPs |
| TF-07 | `appid_qb.protocol_distribution()` | Protocol distribution |
| TF-08 | `appid_qb.egress_interface_breakdown()` | Egress interface breakdown |
| TF-09 | `appid_qb.top_dst_as_countries(size=20)` | Top destination AS countries |
| TF-10 | `appid_qb.top_dst_as_orgs(size=20)` | Top destination AS organizations |

**Auto-interval:** Dynamically selects 1m/5m/15m based on time range size.

### 5.5 Traffic Flow — `app/api/traffic_flow.py` (127 lines)

**Router:** `APIRouter(prefix="/api/v1/traffic-flow", tags=["Traffic Flow"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/traffic-flow/summary` | viewer+ | Per-site flow summary with filters |
| GET | `/api/v1/traffic-flow/chart` | viewer+ | Stacked bar chart data |
| GET | `/api/v1/traffic-flow/table` | viewer+ | Paginated flow records |
| GET | `/api/v1/traffic-flow/sankey` | viewer+ | Sankey diagram data |

**Common Parameters (all endpoints):**
- `site_name` (str, required) — Site to query
- `gte_ms` / `lte_ms` (int, required) — Time range
- `path_filter` (str, default "internet") — Traffic path filter
- `app_filter`, `category_filter`, `client_ip`, `server_ip`, `protocol`, `dst_port` — Optional filters

**Chart-specific:** `bucket_seconds` (int, default 60)
**Table-specific:** `after` (str, optional) — JSON-encoded `search_after` key
**Sankey-specific:** `direction` (str) — "upload" or "download"

### 5.6 Traffic Inbound — `app/api/traffic_inbound.py` (169 lines)

**Router:** `APIRouter(prefix="/api/v1/traffic-inbound", tags=["Traffic Inbound"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/traffic-inbound/summary` | viewer+ | Inbound VIP traffic summary |
| GET | `/api/v1/traffic-inbound/chart` | viewer+ | Stacked bar chart |
| GET | `/api/v1/traffic-inbound/table` | viewer+ | Paginated flow records |
| GET | `/api/v1/traffic-inbound/sankey` | viewer+ | Sankey diagram |

**Constraint:** Only DC and DRC sites (`ALLOWED_SITES = ["Site_FGT-DC", "Site_FGT-DRC"]`). Office site rejected with `INVALID_SITE` error.

**path_filter:** Always `"inbound-vip"` (hardcoded).

### 5.7 Traffic Internal — `app/api/traffic_internal.py` (131 lines)

**Router:** `APIRouter(prefix="/api/v1/traffic-internal", tags=["Traffic Internal"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/traffic-internal/summary` | viewer+ | Internal traffic summary |
| GET | `/api/v1/traffic-internal/chart` | viewer+ | Stacked bar chart |
| GET | `/api/v1/traffic-internal/table` | viewer+ | Paginated flow records |
| GET | `/api/v1/traffic-internal/sankey` | viewer+ | Sankey diagram |

**traffic_path filter:** `"all"` (default), `"intra-lan"`, or `"inter-site"`.

**Sites:** All 3 sites supported.

### 5.8 SD-WAN SLA — `app/api/sdwan.py` (139 lines)

**Router:** `APIRouter(prefix="/api/v1/sdwan", tags=["SD-WAN"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/sdwan/sla` | viewer+ | Latency, jitter, packet loss timelines + link status (FR-03) |

**Parameters:** `gte_ms`, `lte_ms`, `site_name` (default "Site_FGT-DC")

**Flow:**
1. Validate site against `TELEGRAF_SDWAN_SITES` config
2. Pre-fetch validation: check if any SLA data exists (`validate_sla_data()`)
3. Query latency/jitter/packet_loss timelines (3 separate queries)
4. Query current link status
5. Query SLA summary KPIs
6. Return `SDWANResponse`

**Supported Sites:** Site_FGT-DC, Site_FGT-DRC, Site_FGT_Office (4 links each: 2× WAN + 2× MPLS)

### 5.9 HA Status — `app/api/ha.py` (68 lines)

**Router:** `APIRouter(prefix="/api/v1/ha", tags=["HA Status"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/ha/status` | viewer+ | HA cluster health (DC only) |

**Health Logic:**
- **healthy:** `ha_mode != standalone` AND all members `sync_status == 1` (in-sync)
- **degraded:** `ha_mode != standalone` AND any member `sync_status != 1`
- **critical:** `ha_mode == standalone` OR member count < 2

**Only Site_FGT-DC** has HA configured. Other sites return standalone/critical.

### 5.10 Interface Stats — `app/api/interface_stats.py` (187 lines)

**Router:** `APIRouter(prefix="/api/v1/interface-stats", tags=["Interface Stats"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/interface-stats` | viewer+ | Per-interface throughput, speed, operational status |

**Parameters:** `site_name` (required), `gte_ms`, `lte_ms`

**Flow:**
1. Validate site against `SITE_SOURCE_MAP`
2. Single OpenSearch query for all interfaces (Q-07 compliance)
3. Compute throughput deltas from cumulative counters: `Mbps = (current - previous) × 8 / 60 / 1,000,000`
4. Handle counter resets (negative deltas → `None`)
5. Sort by predefined order (WAN interfaces first, then MPLS)

**Hardcoded ifIndex per site:** 4 WAN/MPLS interfaces per site.

### 5.11 Resources — `app/api/resources.py` (129 lines)

**Router:** `APIRouter(prefix="/api/v1/resources", tags=["Resources"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/resources` | viewer+ | CPU/memory/sessions timeline + current status (FR-04) |

**Site Routing:**
- **Site_FGT-DC** (HA): Uses `ha_qb.resource_timeline()` + `ha_qb.current_device_status()` → 2 devices
- **Site_FGT-DRC / Site_FGT_Office** (standalone): Uses `ha_qb.resource_device_status()` + `ha_qb.resource_device_timeline()` → 1 device

### 5.12 VPN Sessions — `app/api/vpn.py` (93 lines)

**Router:** `APIRouter(prefix="/api/v1/vpn", tags=["VPN"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/vpn/ssl` | viewer+ | SSL VPN user sessions |
| GET | `/api/v1/vpn/ipsec` | viewer+ | IPsec VPN user count |

**SSL VPN:** Validates site against `TELEGRAF_SSLVPN_SITES` config. Returns username, device, remote_ip, vpn_ip, bytes in/out.

**IPsec VPN:** Returns username, device, remote_gw_ip, assigned_ip, bytes in/out, tunnel lifetime.

### 5.13 Raw Data — `app/api/raw_data.py` (124 lines)

**Router:** `APIRouter(prefix="/api/v1/traffic", tags=["Raw Data"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/traffic/raw` | **operator+** | Server-side paginated raw flow records (FR-05) |

**Pagination:** `search_after` (not scroll) — Q-04 compliance. Page size max 500.

**Filters:** `client_ip`, `server_ip`, `application` (comma-separated), `category`, `protocol`, `dst_port`, `ingress_zone`, `egress_link`, `site_name`.

**Source Filtering (Q-03):** Only table columns included in `_source`.

### 5.14 Alerts — `app/api/alerts.py` (248 lines)

**Router:** `APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/alerts/rules` | admin+ | List all alert rules |
| POST | `/api/v1/alerts/rules` | admin+ | Create alert rule (201) |
| GET | `/api/v1/alerts/rules/{rule_id}` | admin+ | Get single rule |
| PUT | `/api/v1/alerts/rules/{rule_id}` | admin+ | Update rule |
| DELETE | `/api/v1/alerts/rules/{rule_id}` | admin+ | Delete rule |
| POST | `/api/v1/alerts/rules/{rule_id}/test` | admin+ | Test rule against live data |
| GET | `/api/v1/alerts/logs` | admin+ | Alert firing history (paginated) |
| GET | `/api/v1/alerts/state` | admin+ | Current alert states |

**Test Rule:** Executes rule's query against OpenSearch, returns current metric value and whether threshold is breached. Does NOT fire notification or alter state.

**Activity Logging:** All CRUD operations log activity with rule details.

### 5.15 Reports — `app/api/reports.py` (427 lines)

**Router:** `APIRouter(prefix="/api/v1/reports", tags=["Reports"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/reports` | viewer+ | List report jobs (operator+ sees all) |
| POST | `/api/v1/reports/generate` | **operator+** | Trigger async report generation (202) |
| GET | `/api/v1/reports/status/{job_id}` | viewer+ | Poll report status |
| GET | `/api/v1/reports/download/{job_id}` | **operator+** | Download completed report |
| GET | `/api/v1/reports/preview/{job_id}` | viewer+ | Preview HTML report in browser |
| POST | `/api/v1/reports/distribute/{job_id}` | **operator+** | Send via channels (20MB limit) |
| GET | `/api/v1/reports/schedules` | viewer+ | List report schedules |
| POST | `/api/v1/reports/schedules` | **operator+** | Create schedule |
| PATCH | `/api/v1/reports/schedules/{schedule_id}` | **operator+** | Update schedule |
| DELETE | `/api/v1/reports/schedules/{schedule_id}` | **operator+** | Delete schedule |

**Report Generation Flow:**
1. Create `ReportJob` record (status=pending)
2. Spawn `asyncio.create_task(_generate_report_background(...))`
3. Return 202 with `job_id`
4. Background task: update status to running → call `generate_report()` → update status to completed/failed
5. Reports expire after 1 hour (`REPORT_TTL_HOURS = 1`)

**Distribution Channels:** email, telegram, discord, whatsapp

**File Size Guard:** Reports > 20MB cannot be distributed automatically.

### 5.16 Users — `app/api/users.py` (206 lines)

**Router:** `APIRouter(prefix="/api/v1/users", tags=["Users"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/users/me` | any authenticated | Get own profile |
| PUT | `/api/v1/users/me` | any authenticated | Update own profile (name, email only) |
| PUT | `/api/v1/users/me/password` | any authenticated | Change own password |
| GET | `/api/v1/users` | **admin+** | List all users (paginated) |
| POST | `/api/v1/users` | **admin+** | Create user (201) |
| PUT | `/api/v1/users/{user_id}` | **admin+** | Update user |
| DELETE | `/api/v1/users/{user_id}` | **admin+** | Hard-delete user |

**Safety Guards:**
- Admin cannot modify/delete superadmin accounts
- Users cannot delete their own account
- Username and email uniqueness checked on create
- Password change resets `must_change_password` flag

### 5.17 Logs — `app/api/logs.py` (71 lines)

**Router:** `APIRouter(prefix="/api/v1/logs", tags=["Logs"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/logs/user-activity` | **superadmin only** | User activity audit log (FR-11) |

**Parameters:** `limit` (max 200), `offset`, `user_id` (optional filter)

**N+1 Prevention (Q-07):** Batch-fetches all referenced users in a single query using `User.id.in_(user_ids)`, then maps results in memory.

### 5.18 Notifications — `app/api/notifications.py` (82 lines)

**Router:** `APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])`

| Method | Path | RBAC | Description |
|--------|------|------|-------------|
| GET | `/api/v1/notifications` | any authenticated | Fetch user notifications |
| PATCH | `/api/v1/notifications/{notification_id}/read` | any authenticated | Mark as read |
| POST | `/api/v1/notifications/mark-all-read` | any authenticated | Mark all as read |

**Parameters:** `unread_only` (bool, default False), `limit` (default 50), `offset`

---

## 6. OpenSearch Query Builders — 10 Modules

All query builders follow the OpenSearch compliance mandates (Q-01 through Q-08).

### 6.1 Client Factory — `app/opensearch/client.py` (82 lines)

Three singleton-style client factories (LRU-cached):

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `get_dc_client()` | 10.80.150.108:9200 | DC cluster |
| `get_drc_client()` | 10.90.150.108:9200 | DRC cluster (shared with Office) |
| `get_ipsec_client()` | 10.90.150.108:9200 | IPsec index |

**Client Configuration:**
- HTTPS auto-detection from URL scheme
- `verify_certs=False`, `ssl_show_warn=False` (internal/self-signed certs)
- Connection pool: `maxsize=10`
- `retry_on_timeout=True`, `max_retries=2`
- Optional HTTP basic auth

**Health Check Functions:**
- `check_opensearch_health(client)` → `bool`
- `check_all_clusters()` → `dict[str, bool]`

### 6.2 AppID Query Builder — `app/opensearch/appid.py` (737 lines)

**Index:** `fortigate-appid-flow-*`  
**Client:** `get_drc_client()` (DC-only legacy module)

**Functions:**

| Function | Purpose | Key Aggregations |
|----------|---------|-----------------|
| `top_applications(gte_ms, lte_ms, size)` | Top N apps by bytes | `terms` on `flow.application.name` + `sum` script (client+server bytes) |
| `application_categories(gte_ms, lte_ms)` | App categories | `terms` on `flow.application.category` |
| `throughput_timeline(gte_ms, lte_ms, interval)` | Throughput over time | `date_histogram` + `sum` script |
| `top_dst_as_orgs(gte_ms, lte_ms, size)` | Top AS organizations | `terms` on `flow.dst.as.org` |
| `top_dst_as_countries(gte_ms, lte_ms, size)` | Top AS countries | `terms` on `flow.dst.as.country` |
| `protocol_distribution(gte_ms, lte_ms)` | Protocol breakdown | `terms` on `flow.protocol` |
| `raw_flow_records(gte_ms, lte_ms, size)` | Raw flow records | `search_after` pagination |
| `total_throughput(gte_ms, lte_ms)` | Total bytes | `sum` script |
| `top_client_ips(gte_ms, lte_ms, size)` | Top client IPs | `terms` on `flow.client.ip.addr` |
| `top_server_ips(gte_ms, lte_ms, size)` | Top server IPs | `terms` on `flow.server.ip.addr` |
| `egress_interface_breakdown(gte_ms, lte_ms)` | Egress interfaces | `terms` on `flow.out.netif.alias` |
| `sankey_data(gte_ms, lte_ms, size)` | Sankey nodes/links | `composite` agg (ingress→app→egress) + sub-agg `by_as_country` |

**Common Filters:**
- `_time_range(gte_ms, lte_ms)` — Q-01 compliance
- `_exclude_app0()` — excludes `application.name = "app-0"` (unclassified)
- `_exclude_private_as()` — excludes `dst.as.org = "Private"` (internal)

### 6.3 Traffic Flow Query Builder — `app/opensearch/traffic_flow.py` (436 lines)

**Index:** `fortigate-appid-flow-*`  
**Site Routing:** DC → DC cluster, DRC + Office → DRC cluster  
**Site Filter:** `flow.export.ip.addr = source_ip` (per-site source IP)

**Functions:**

| Function | Purpose | Key Features |
|----------|---------|-------------|
| `flow_summary(...)` | 9 widget aggregations | Terms aggs for apps, categories, AS orgs, clients, servers, protocols, egress |
| `flow_chart(...)` | Stacked bar chart | `date_histogram` + `terms` sub-agg per app |
| `flow_table(...)` | Paginated flow records | `composite` aggregation with `search_after` |
| `sankey_data(...)` | Sankey diagram | `composite` agg (zone→app→egress) + AS country sub-agg |

**Filters:** `path_filter`, `app_filter`, `category_filter`, `client_ip`, `server_ip`, `protocol`, `dst_port`

### 6.4 Traffic Inbound Query Builder — `app/opensearch/traffic_inbound.py` (418 lines)

**Index:** `fortigate-appid-flow-*`  
**path_filter:** Always `"inbound-vip"`  
**Sites:** DC and DRC only

**Same function signatures as traffic_flow.py** but with `PORT_SERVICE_MAP` integration for port→service name resolution.

**Functions:** `flow_summary`, `flow_chart`, `flow_table`, `sankey_data`

### 6.5 Traffic Internal Query Builder — `app/opensearch/traffic_internal.py` (404 lines)

**Index:** `fortigate-appid-flow-*`  
**path_filter:** `"intra-lan"` or `"inter-site"` (or "all")  
**Sites:** All 3 sites

**Special Function:** `_resolve_service_filter()` — maps service name text to port numbers using `SVC_NAME_TO_PORT`.

**Functions:** `flow_summary`, `flow_chart`, `flow_table`, `sankey_data`

### 6.6 SD-WAN Query Builder — `app/opensearch/sdwan.py` (384 lines)

**Index:** `telegraf-index*`  
**Q-06 Compliance:** Exact `measurement_name.keyword` term filter per site

**Configuration (from schemas):**
- `SITE_LINK_LABELS` — Display labels per site per link
- `SITE_LINK_COUNT` — Links per site (default 4)
- `SITE_OS_ENDPOINT` — Which cluster each site lives on
- `SITE_LINK_TYPES` — WAN/MPLS classification per link

**Functions:**

| Function | Purpose | Key Aggregations |
|----------|---------|-----------------|
| `validate_sla_data(gte_ms, lte_ms, site_name)` | Pre-fetch validation | `size=1`, `track_total_hits=True` |
| `sla_timeline(gte_ms, lte_ms, site_name, metric, interval)` | SLA metric timeline | `date_histogram` + `avg` per link |
| `sdwan_link_status(gte_ms, lte_ms, site_name)` | Current link status | `top_hits` (latest document) |
| `sla_summary(gte_ms, lte_ms, site_name)` | Summary KPIs | `avg` + `max` aggregations per link |
| `all_sites_link_status(gte_ms, lte_ms, site_names)` | Multi-site link status | `terms` on `measurement_name.keyword` + `top_hits` per site |

**Metric Fields:** `{site_name}.latency_link{N}`, `{site_name}.jitter_link{N}`, `{site_name}.packet_loss_link{N}`

### 6.7 HA Query Builder — `app/opensearch/ha.py` (487 lines)

**Index:** `telegraf-index*`

**Site-Specific Behavior:**
- **DC:** Uses `ha_member` measurement (2 devices in HA cluster) + `Site_FGT-DC_HA` for cluster config
- **DRC/Office:** Uses `Resource_FGT-*` measurements (single device)

**Functions:**

| Function | Purpose |
|----------|---------|
| `resource_timeline(gte_ms, lte_ms, interval)` | CPU/mem/sessions timeline per device (DC only) |
| `current_device_status(gte_ms, lte_ms)` | Current resource status per device |
| `ha_cluster_status(site_name)` | HA cluster health + member details |
| `ha_overview(gte_ms, lte_ms)` | Aggregated HA overview |
| `session_sparkline(gte_ms, lte_ms)` | Session count sparkline data |
| `resource_device_status(site_name, gte_ms, lte_ms)` | Single device status (DRC/Office) |
| `resource_device_timeline(site_name, gte_ms, lte_ms, interval)` | Single device timeline (DRC/Office) |

### 6.8 Interface Stats Query Builder — `app/opensearch/interface_stats.py` (183 lines)

**Index:** `telegraf-index*`, `measurement=fgt_iface_stats`

**Hardcoded ifIndex per site:** 4 WAN/MPLS interfaces per site (defined in `SITE_IFINDEX_MAP` and `SITE_IFACE_SORT_ORDER`).

**Functions:**

| Function | Purpose |
|----------|---------|
| `interface_stats_timeline(gte_ms, lte_ms, site_name)` | Per-interface throughput timeline |

**Aggregations:** `terms` on `ifIndex` → `date_histogram` → `max` on `inOctets`/`outOctets`/`speed`/`ifOperStatus`

### 6.9 SSL VPN Query Builder — `app/opensearch/sslvpn.py` (177 lines)

**Index:** `telegraf-index*`, measurement per SSL VPN site

**Functions:**

| Function | Purpose |
|----------|---------|
| `active_sslvpn_users_count(gte_ms, lte_ms, site_names)` | Total active SSL VPN users across sites |
| `active_sslvpn_users_detail(gte_ms, lte_ms, site_name)` | Per-user session details |
| `sslvpn_timeline(gte_ms, lte_ms, site_name)` | User count timeline |

### 6.10 IPsec Query Builder — `app/opensearch/ipsec.py` (120 lines)

**Index:** `ipsec-*`

**Functions:**

| Function | Purpose |
|----------|---------|
| `active_ipsec_users_count(gte_ms, lte_ms)` | Total active IPsec VPN users |
| `active_ipsec_users_detail(gte_ms, lte_ms)` | Per-user session details |

---

## 7. Services Layer

### 7.1 Alert Engine — `app/services/alert_engine.py` (262 lines)

**Scheduler:** APScheduler `AsyncIOScheduler`, polls every 60 seconds (`ALERT_POLL_INTERVAL_SECONDS`).

**State Machine:**
```
INACTIVE → PENDING → FIRING → RESOLVED
                  ↑         │
                  └─────────┘  (re-notify while FIRING)
```

**Evaluation Cycle (`evaluate_all_rules()`):**
1. Load all enabled `AlertRule` records from DB
2. For each rule:
   a. Execute `_evaluate_single_rule()` — queries OpenSearch for current metric value
   b. Check condition against threshold via `_check_condition()`
   c. Load/create `AlertState` record
   d. Apply state transitions:
      - **INACTIVE → PENDING:** Condition first met
      - **PENDING → FIRING:** Condition sustained for `sustained_for_minutes`
      - **FIRING → RESOLVED:** Condition no longer met
      - **FIRING (re-notify):** Re-notify after `ALERT_RENOTIFY_INTERVAL_MINUTES`
   e. On FIRING: Create `AlertLog`, dispatch notifications, WebSocket broadcast
   f. On RESOLVED: Update `AlertLog.resolved_at`, WebSocket broadcast

**Supported Data Sources:**
- `ha_resource` — queries `ha_qb.current_device_status()`
- `appid_flow` — queries `appid_qb.total_throughput()`

**Notification Dispatch (`_notify()`):**
- Formats alert message with severity, metric value, condition, threshold
- Dispatches to configured channels: `telegram`, `email`

### 7.2 Report Generator — `app/services/report_generator.py` (~535 lines)

**8 Report Types (R-01 through R-08):**

| ID | Type | Sections |
|----|------|----------|
| R-01 | Traffic Overview | Top apps, throughput, AS, countries, protocol, per-site |
| R-02 | Resource Usage | Per-device CPU, memory, sessions, HA status |
| R-03 | VPN Users | SSL VPN & IPsec VPN active user counts |
| R-04 | SD-WAN SLA | Latency, jitter, packet loss, link status per site |
| R-05 | Traffic Inbound | Top services, client AS, countries, egress interfaces |
| R-06 | Traffic Internal | Intra-LAN, inter-site flow, top services |
| R-07 | Executive Summary | KPI dashboard, 1-page overview |
| R-08 | All-in-One | Combined report: all sections |

**Output Formats:**
- **PDF** — WeasyPrint (HTML → PDF)
- **HTML** — Jinja2 external template (`reports/templates/report_base.html`)
- **DOCX** — python-docx programmatic generation (`scripts/generate_docx.py`)

**Template:** External Jinja2 template at `reports/templates/report_base.html` (1,292 lines). Follows NOD design system with WeasyPrint-compatible CSS (@page A4, header/footer, page breaks). Loaded via `FileSystemLoader`.

**DOCX Generator:** `scripts/generate_docx.py` (1,610 lines). Programmatic Word document generation matching the HTML/PDF visual design. Includes 30 helper functions for XML manipulation, layout, and styling.

**Chart Integration:** Uses `chart_renderer.py` to embed Matplotlib PNG charts as base64 data URIs.

**Known Issue:** R-01 uses `appid_qb` (DC only), ignores `sites` and `sections` parameters.

### 7.3 Report Scheduler — `app/services/report_scheduler.py` (151 lines)

**Scheduler:** APScheduler `AsyncIOScheduler`, checks every 60 seconds.

**Flow:**
1. Query enabled `ReportSchedule` records where `next_run_at <= now`
2. For each due schedule:
   a. Create `ReportJob` record
   b. Call `generate_report()` synchronously within the scheduler job
   c. Auto-distribute if channels configured
   d. Update `last_run_at` and calculate `next_run_at`

**Cron Calculator (`_calculate_next_run()`):** Supports daily at specific time and interval-based patterns.

**Known Issue:** `report_schedules` table may not be migrated (see Section 10).

### 7.4 Chart Renderer — `app/services/chart_renderer.py` (285 lines)

**Backend:** Matplotlib `Agg` (non-interactive)

**Chart Types:**

| Function | Purpose | Returns |
|----------|---------|---------|
| `render_timeseries_chart(data, title, ylabel, ...)` | Line chart with optional multi-series | PNG bytes |
| `render_bar_chart(labels, values, title, ...)` | Horizontal/vertical bar chart | PNG bytes |
| `render_vpn_bar_chart(ssl_count, ipsec_count, ...)` | VPN user comparison | PNG bytes |
| `render_pie_chart(labels, values, title, ...)` | Pie chart for distributions | PNG bytes |
| `render_gauge_chart(label, value, max_value, ...)` | Donut gauge for KPIs | PNG bytes |
| `render_stacked_area_chart(data, series_names, ...)` | Stacked area for throughput | PNG bytes |

**Common Features:**
- All return `bytes` (PNG) for embedding
- Timestamps auto-converted from epoch millis to `datetime`
- Color palette: `#2563eb`, `#7c3aed`, `#f59e0b`, `#10b981`, `#ef4444`, `#06b6d4`
- DPI: 150 (configurable)

### 7.5 Activity Logger — `app/services/activity_logger.py` (35 lines)

```python
async def log_activity(user_id: str, action: str, source_ip: str = None, details: dict = None) -> None:
```

Fire-and-forget: writes `UserActivityLog` entries. Silent failure — never crashes the request.

### 7.6 WebSocket Manager — `app/services/websocket_manager.py` (65 lines)

```python
class AlertWebSocketManager:
    _connections: dict[str, WebSocket]  # user_id → websocket
```

**Methods:**
- `connect(ws, user_id)` — Accept connection, store by user_id
- `disconnect(user_id)` — Remove connection
- `broadcast(message, user_id=None)` — Send to all or specific user. Dead connection cleanup on failure.
- `active_connections` — Property: count of connected clients
- `is_connected(user_id)` — Check if user has active connection

**Singleton:** `alert_ws_manager = AlertWebSocketManager()`

### 7.7 Notifiers

#### Email — `app/services/notifiers/email.py` (101 lines)

| Function | Purpose |
|----------|---------|
| `send_email_alert(subject, body, recipient)` | Plain text alert email |
| `send_email_with_attachment(subject, body, file_path, recipient)` | Email with file attachment |

Uses `aiosmtplib` with TLS. Skips silently if SMTP not configured.

#### Telegram — `app/services/notifiers/telegram.py` (65 lines)

| Function | Purpose |
|----------|---------|
| `send_telegram_alert(message)` | Text alert via Bot API |
| `send_telegram_document(file_path, caption)` | Document upload via Bot API |

Uses `httpx` async client. Markdown parse mode for formatting.

#### Discord — `app/services/notifiers/discord.py` (64 lines)

| Function | Purpose |
|----------|---------|
| `send_discord_message(message)` | Text message via Webhook |
| `send_discord_file(file_path, message)` | File upload via Webhook |

Uses `httpx` with multipart/form-data for file uploads.

#### WhatsApp — `app/services/notifiers/whatsapp.py` (99 lines)

| Function | Purpose |
|----------|---------|
| `send_whatsapp_message(message, recipient_phone)` | Text via Business Cloud API |
| `send_whatsapp_document(file_path, caption, recipient_phone)` | Document via media upload + send |

Uses Facebook Graph API v19.0. Two-step flow for documents: upload media → send message with media_id.

---

## 8. Schema System (Pydantic Models)

All schemas use Pydantic v2 (`BaseModel`) with `model_config = {"from_attributes": True}` for ORM compatibility.

### 8.1 Common — `app/schemas/common.py` (50 lines)

```python
class APIResponse(BaseModel, Generic[T]):
    success: bool
    data: Optional[T] = None
    meta: Optional[Meta] = None
    error: Optional[ErrorDetail] = None
```

**Factory Methods:**
- `APIResponse.ok(data, meta)` — success=True
- `APIResponse.fail(code, message)` — success=False with error detail

```python
class Meta(BaseModel):
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None
    query_took_ms: Optional[int] = None
```

### 8.2 User Schemas — `app/schemas/user.py` (70 lines)

| Schema | Purpose |
|--------|---------|
| `LoginRequest` | username + password |
| `TokenResponse` | access_token + token_type |
| `RefreshRequest` | Empty (token from cookie) |
| `ChangePasswordRequest` | current_password + new_password (min 8) |
| `UserBase` | username, email, full_name, role (pattern-validated) |
| `UserCreate(UserBase)` | + password (min 8) |
| `UserUpdate` | Optional fields for partial update |
| `UserRead(UserBase)` | + id, is_active, must_change_password, last_login, timestamps |
| `UserListResponse` | users list + total count |

### 8.3 Overview Schemas — `app/schemas/overview.py` (120 lines)

| Schema | Description |
|--------|-------------|
| `ActiveUserKPI` | active_users + label |
| `SparklinePoint` | timestamp + value |
| `DeviceResourceStatus` | device, hostname, serial, CPU/mem/sessions, sync_status, sparkline |
| `TopApplication` | application, total_bytes, bytes_human |
| `TopASOrg` | org_name, total_bytes, bytes_human |
| `WanLinkStatus` | link, link_name, status |
| `SiteWanStatus` | site, device, links |
| `ThroughputKPI` | total_bytes, bytes_human |
| `HAStatusKPI` | ha_mode, member_count, overall_health |
| `WanInterfaceSummary` | label, in/out Mbps, speed, oper_status |
| `SiteWanBandwidth` | site, interfaces |
| `TopInboundService` | service_name, total_bytes, bytes_human |
| `OverviewResponse` | Complete dashboard response with all panels |

### 8.4 Traffic Schemas — `app/schemas/traffic.py` (125 lines)

| Schema | Description |
|--------|-------------|
| `TopApplicationItem` | application, total_bytes, bytes_human |
| `CategoryItem` | category, total_bytes, bytes_human |
| `SankeyNode` | id, label |
| `SankeyLink` | source, target, value |
| `SankeyData` | nodes, links, as_country_nodes/links |
| `ThroughputPoint` | timestamp (epoch ms), bytes |
| `TopIPItem` | ip, total_bytes, bytes_human |
| `ProtocolItem` | protocol, total_bytes, total_packets |
| `EgressInterfaceItem` | interface, total_bytes, bytes_human |
| `ASCountryItem` | country, total_bytes, bytes_human |
| `ASOrgItem` | as_org, as_number, total_bytes, bytes_human, country |
| `TrafficSummaryResponse` | All 10 traffic analytics panels |
| `RawFlowRecord` | Full flow record with correlation fields |
| `RawFlowFilterParams` | Filter parameters for raw data queries |

### 8.5 Traffic Flow Schemas — `app/schemas/traffic_flow.py` (132 lines)

| Schema | Description |
|--------|-------------|
| `SITE_FLOW_CONFIG` | Per-site source IP + endpoint routing dict |
| `TopAppItem` | app_name, total_bytes, speed_mbps, percentage |
| `AppCategoryItem` | category_name, total_bytes, count |
| `TopASOrgItem` | org_name, total_bytes |
| `TopASCountryItem` | country, total_bytes, flag_code |
| `TopClientItem` | ip, total_bytes |
| `TopServerItem` | ip, total_bytes, hostname |
| `ProtocolDistItem` | protocol, total_bytes, percentage |
| `EgressBreakdownItem` | interface, total_bytes |
| `TrafficSummaryResponse` | 8-widget summary |
| `TrafficChartResponse` | chart_data + app_names + global_speed_by_app |
| `FlowTableRecord` | client_ip, server_ip, app_name, bytes, packets, sessions |
| `TrafficTableResponse` | records + after_key |
| `SankeyNode/Link/Response` | Sankey diagram data |

### 8.6 Traffic Inbound Schemas — `app/schemas/traffic_inbound.py` (113 lines)

Similar to traffic_flow but with `TopServiceItem` (service_name + service_port) instead of `TopAppItem`.

### 8.7 Traffic Internal Schemas — `app/schemas/traffic_internal.py` (84 lines)

Similar structure with `InterfaceBreakdownItem` for ingress/egress breakdown.

### 8.8 SD-WAN/Resource/VPN Schemas — `app/schemas/sdwan_resource_vpn.py` (208 lines)

**SD-WAN Configuration Constants:**
- `SITE_LINK_LABELS` — Display labels per link per site (e.g., "WAN LinkNet", "MPLS iForte")
- `SITE_LINK_COUNT` — Links per site (4)
- `SITE_OS_ENDPOINT` — OpenSearch endpoint routing per site
- `SITE_LINK_TYPES` — WAN/MPLS classification

**SD-WAN Schemas:**
- `LinkMetricPoint` — timestamp, value, label, link_type
- `SLATimeline` — flattened list of LinkMetricPoints
- `LinkCurrentStatus` — link, ifname, label, link_type, status, sla_target
- `SiteSLAStatus` — site, device, links
- `SLASummaryKPI` — avg/max latency, avg jitter, avg packet_loss (per link)
- `SDWANResponse` — Complete SD-WAN response

**Resource Schemas:**
- `ResourcePoint` — timestamp, value, device
- `ResourceTimeline` — cpu, memory, sessions lists
- `DeviceCurrentResource` — device, hostname, serial, CPU/mem/sessions, sync_status
- `ResourceResponse` — timeline + current

**VPN Schemas:**
- `SSLVPNUser` — username, device, remote_ip, vpn_ip, bytes in/out
- `IPsecVPNUser` — username, device, remote_gw_ip, assigned_ip, bytes in/out, tunnel_lifetime
- `VPNSessionsResponse` — ssl_vpn + ipsec_vpn lists

**HA Schemas:**
- `HAMember` — memberIndex, role, syncStatus, priority, hostname
- `HAResponse` — ha_mode, members, overallHealth, message

### 8.9 Alert Schemas — `app/schemas/alert.py` (80 lines)

| Schema | Purpose |
|--------|---------|
| `AlertRuleCreate` | Full rule creation with field validation |
| `AlertRuleUpdate` | Partial update (all Optional) |
| `AlertRuleRead` | Complete rule with id, timestamps |
| `AlertTestResult` | rule_id, current_metric_value, threshold_breached, query_took_ms |
| `AlertLogRead` | Alert log entry with rule snapshot |

**Field Validation:**
- `severity`: `^(INFO|WARNING|CRITICAL)$`
- `data_source`: `^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec)$`
- `aggregation`: `^(avg|max|min|sum|count)$`
- `condition`: `^(>|<|>=|<=|==)$`

### 8.10 Notification Schemas — `app/schemas/notification.py` (19 lines)

```python
class NotificationRead(BaseModel):
    id: str
    alert_name: str
    severity: str
    message: str
    is_read: bool
    created_at: datetime
```

### 8.11 Report Schemas — `app/schemas/report.py` (44 lines)

| Schema | Purpose |
|--------|---------|
| `ReportGenerateRequest` | report_type (R-01..R-08), output_format, time range, sites, sections |
| `ReportJobStatus` | job_id, report_type, status, file info, timestamps |
| `ReportDistributeRequest` | channels, recipient_email, recipient_phone |

---

## 9. Data Flow Architecture

### 9.1 Request Lifecycle

```
Client Request
    │
    ▼
FastAPI Router ──▶ Depends(get_current_user) ──▶ JWT validation
    │                                                  │
    │                                          ┌───────┘
    │                                          ▼
    │                                    User lookup in DB
    │                                          │
    ▼                                          ▼
Route Handler ◀──────────────────────────── RBAC check (require_role)
    │
    ├──▶ OpenSearch Query Builder ──▶ AsyncOpenSearch client ──▶ OpenSearch cluster
    │         │
    │         ▼
    │    Raw aggregation response
    │         │
    │         ▼
    │    Transform to Pydantic schema
    │
    ├──▶ DB query (if needed)
    │
    ▼
APIResponse.ok(data=..., meta={"query_took_ms": ...})
    │
    ▼
JSON Response
```

### 9.2 Background Task Flow

```
API Request (POST /reports/generate)
    │
    ▼
Create ReportJob (status=pending) ──▶ Return 202
    │
    ▼
asyncio.create_task(_generate_report_background())
    │
    ▼
Update status → running
    │
    ├──▶ generate_report(job) ──▶ OpenSearch queries
    │         │
    │         ▼
    │    chart_renderer (Matplotlib)
    │         │
    │         ▼
    │    Jinja2 template rendering
    │         │
    │         ▼
    │    WeasyPrint / python-docx
    │         │
    │         ▼
    │    Save to reports/output/
    │
    ▼
Update status → completed (or failed)
```

### 9.3 Alert Evaluation Flow

```
APScheduler (every 60s)
    │
    ▼
evaluate_all_rules()
    │
    ├──▶ Load enabled AlertRule records
    │
    ▼
For each rule:
    │
    ├──▶ _evaluate_single_rule() ──▶ OpenSearch query
    │         │
    │         ▼
    │    metric_value
    │         │
    │         ▼
    ├──▶ _check_condition(value, op, threshold)
    │         │
    │    ┌────┴────┐
    │    │         │
    │  True      False
    │    │         │
    │    ▼         ▼
    │  State      State → RESOLVED
    │  machine    │
    │  transitions ▼
    │    │      WebSocket broadcast
    │    ▼
    │  FIRING → Create AlertLog
    │         → _notify() (Email, Telegram)
    │         → WebSocket broadcast
    │
    ▼
db.commit()
```

### 9.4 WebSocket Alert Flow

```
Frontend                    Backend                       Alert Engine
    │                           │                              │
    │  WS /ws/alerts?token=JWT  │                              │
    │──────────────────────────▶│                              │
    │                           │  ws_get_current_user(token)  │
    │                           │◀─────────────────────────────│
    │                           │                              │
    │  Connection established   │                              │
    │◀──────────────────────────│                              │
    │                           │                              │
    │                           │     evaluate_all_rules()     │
    │                           │◀─────────────────────────────│
    │                           │                              │
    │                           │  alert_ws_manager.broadcast()│
    │                           │◀─────────────────────────────│
    │                           │                              │
    │  {"type":"alert_firing",..}│                              │
    │◀──────────────────────────│                              │
```

### 9.5 Port Service Map — `app/port_service_map.py` (209 lines)

**Single source of truth** for port→service name resolution.

- `PORT_SERVICE_MAP: dict[int, str]` — 200+ port-to-name mappings (IANA well-known + infrastructure services)
- `SVC_NAME_TO_PORT: dict[str, int]` — Reverse lookup (lowercase name → port)

**Examples:**
```python
PORT_SERVICE_MAP = {
    22: "SSH", 80: "HTTP", 443: "HTTPS", 3306: "MySQL",
    5432: "PostgreSQL", 8443: "FortiGate-Mgmt", ...
}
```

Used by: `traffic_inbound.py`, `traffic_internal.py`, `raw_data.py` query builders.

---

## 10. Known Issues & Technical Debt

### 10.1 Missing Migration for `report_schedules`

The `ReportSchedule` model is defined in `models.py` and the initial migration creates the table, but the `report_scheduler.py` service references it. The migration file does include `report_schedules` in its table list, so this should be functional.

### 10.2 `user_preferences` and `user_pinned_widgets` Not Migrated

Two models (`UserPreference`, `UserPinnedWidget`) are defined in `models.py` but NOT included in the initial Alembic migration. These tables will not exist in the database until a new migration is created.

### 10.3 Report R-01 DC-Only Limitation

`report_generator.py` R-01 uses `appid_qb` which queries only the DC cluster. It ignores the `sites` and `sections` parameters from `ReportGenerateRequest`.

### 10.4 Alert Engine Limited Data Sources

The alert engine (`_evaluate_single_rule`) only supports `ha_resource` and `appid_flow` data sources. Other data sources (`sdwan_sla`, `vpn_ssl`, `vpn_ipsec`) are listed in the schema but not implemented in the evaluation logic.

### 10.5 Dead Code in `create_alert_rule`

In `app/api/alerts.py`, the activity logging code after `return APIResponse.ok(...)` on line 72-79 is unreachable dead code (appears after a `return` statement).

### 10.6 Duplicate `_format_bytes_auto` in chart_renderer

`app/services/chart_renderer.py` defines `_format_bytes_auto()` twice (lines 19-27 and 44-52). The second definition shadows the first.

### 10.7 Schema Redefinitions in sdwan_resource_vpn.py

`LinkMetricPoint`, `SLATimeline`, `LinkCurrentStatus`, `SiteSLAStatus`, and `SLASummaryKPI` are defined twice in `app/schemas/sdwan_resource_vpn.py` (lines 56-88 and 90-122). The second definitions shadow the first, and have different field structures (e.g., `SLATimeline` changes from `link1/link2` fields to `links` list).

### 10.8 WebSocket Authentication Limitation

The WebSocket endpoint uses JWT via query parameter (`?token=JWT`), which may appear in server logs and browser history. Consider using the `Sec-WebSocket-Protocol` header for production.

### 10.9 No Rate Limiting

No rate limiting is implemented on any endpoint. The `/auth/login` endpoint is particularly vulnerable to brute-force attacks.

### 10.10 Report File Cleanup

Report files are stored in `reports/output/` with a TTL of 1 hour, but there is no background job to actually delete expired files. The `expires_at` field is set but never checked for cleanup.

---

## 11. File Inventory

### 11.1 Complete File Listing (55 files, ~8,500 lines)

```
backend/
├── Dockerfile                                    (35 lines)
├── requirements.txt                              (48 lines)
├── alembic/
│   ├── env.py                                    (66 lines)
│   └── versions/
│       └── 08bcffb3a374_initial_schema.py       (196 lines)
├── scripts/
│   └── seed_superadmin.py                        (144 lines)
└── app/
    ├── __init__.py                                (1 line)
    ├── main.py                                   (250 lines)
    ├── core/
    │   ├── __init__.py                            (1 line)
    │   ├── config.py                             (107 lines)
    │   ├── logging.py                             (82 lines)
    │   └── security.py                            (99 lines)
    ├── db/
    │   ├── __init__.py                            (1 line)
    │   ├── models.py                             (376 lines)
    │   └── session.py                             (44 lines)
    ├── api/
    │   ├── __init__.py                            (1 line)
    │   ├── auth.py                               (263 lines)
    │   ├── overview.py                           (253 lines)
    │   ├── traffic.py                            (134 lines)
    │   ├── traffic_flow.py                       (127 lines)
    │   ├── traffic_inbound.py                    (169 lines)
    │   ├── traffic_internal.py                   (131 lines)
    │   ├── sdwan.py                              (139 lines)
    │   ├── ha.py                                  (68 lines)
    │   ├── interface_stats.py                    (187 lines)
    │   ├── resources.py                          (129 lines)
    │   ├── vpn.py                                 (93 lines)
    │   ├── raw_data.py                           (124 lines)
    │   ├── alerts.py                             (248 lines)
    │   ├── reports.py                            (427 lines)
    │   ├── users.py                              (206 lines)
    │   ├── logs.py                                (71 lines)
    │   └── notifications.py                       (82 lines)
    ├── schemas/
    │   ├── __init__.py                            (1 line)
    │   ├── common.py                              (50 lines)
    │   ├── user.py                                (70 lines)
    │   ├── overview.py                           (120 lines)
    │   ├── traffic.py                            (125 lines)
    │   ├── traffic_flow.py                       (132 lines)
    │   ├── traffic_inbound.py                    (113 lines)
    │   ├── traffic_internal.py                    (84 lines)
    │   ├── sdwan_resource_vpn.py                 (208 lines)
    │   ├── alert.py                               (80 lines)
    │   ├── notification.py                        (19 lines)
    │   └── report.py                              (44 lines)
    ├── opensearch/
    │   ├── __init__.py                            (1 line)
    │   ├── client.py                              (82 lines)
    │   ├── appid.py                              (737 lines)
    │   ├── traffic_flow.py                       (436 lines)
    │   ├── traffic_inbound.py                    (418 lines)
    │   ├── traffic_internal.py                   (404 lines)
    │   ├── sdwan.py                              (384 lines)
    │   ├── ha.py                                 (487 lines)
    │   ├── interface_stats.py                    (183 lines)
    │   ├── sslvpn.py                             (177 lines)
    │   └── ipsec.py                              (120 lines)
    ├── services/
    │   ├── __init__.py                            (1 line)
    │   ├── alert_engine.py                       (262 lines)
    │   ├── report_generator.py                  (~535 lines)
    │   ├── report_scheduler.py                   (151 lines)
    │   ├── chart_renderer.py                     (285 lines)
    │   ├── activity_logger.py                     (35 lines)
    │   ├── websocket_manager.py                   (65 lines)
    │   └── notifiers/
    │       ├── __init__.py                        (1 line)
    │       ├── email.py                          (101 lines)
    │       ├── telegram.py                        (65 lines)
    │       ├── discord.py                         (64 lines)
    │       └── whatsapp.py                        (99 lines)
    ├── port_service_map.py                       (209 lines)
    └── reports/
        └── templates/
            └── report_base.html                (1,292 lines)
scripts/
    └── generate_docx.py                       (1,610 lines)
```

### 11.2 Line Count Summary

| Module | Files | Lines |
|--------|-------|-------|
| Infrastructure (Dockerfile, requirements, alembic, scripts) | 4 | 443 |
| Core (config, logging, security) | 3 | 288 |
| Database (models, session) | 2 | 420 |
| API Routes | 17 | 2,886 |
| Schemas | 11 | 1,045 |
| OpenSearch Query Builders | 10 | 3,448 |
| Services | 7 | 2,138 |
| Port Service Map | 1 | 209 |
| Report Template (HTML) | 1 | 1,292 |
| DOCX Generator (scripts) | 1 | 1,610 |
| **Total** | **57** | **~13,700** |

### 11.3 API Endpoint Summary (42 endpoints)

| Category | Endpoints | RBAC |
|----------|-----------|------|
| Authentication | 3 | Public / Authenticated |
| Overview | 1 | viewer+ |
| Traffic (Legacy) | 1 | viewer+ |
| Traffic Flow | 4 | viewer+ |
| Traffic Inbound | 4 | viewer+ |
| Traffic Internal | 4 | viewer+ |
| SD-WAN | 1 | viewer+ |
| HA Status | 1 | viewer+ |
| Interface Stats | 1 | viewer+ |
| Resources | 1 | viewer+ |
| VPN | 2 | viewer+ |
| Raw Data | 1 | operator+ |
| Alerts | 8 | admin+ |
| Reports | 10 | viewer+ / operator+ |
| Users | 7 | any / admin+ |
| Logs | 1 | superadmin |
| Notifications | 3 | any authenticated |
| Health | 1 | Public |
| Root | 1 | Public |
| **Total** | **55** | — |

---

*Report generated from source analysis of NOD Backend v1.0.0*
