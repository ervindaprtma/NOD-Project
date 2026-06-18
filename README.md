# NOD — Network Observability Dashboard

**Enterprise-grade FortiGate network observability platform for NOC teams.**

---

## Daftar Isi

1. [Ringkasan Proyek](#1-ringkasan-proyek)
2. [Arsitektur Sistem](#2-arsitektur-sistem)
3. [Arsitektur Kode](#3-arsitektur-kode)
   - [Struktur Direktori Lengkap](#31-struktur-direktori-lengkap)
   - [Backend — Python / FastAPI](#32-backend--python--fastapi)
   - [Frontend — Next.js 14+ / TypeScript](#33-frontend--nextjs-14--typescript)
   - [Infrastruktur — Docker Compose / Nginx](#34-infrastruktur--docker-compose--nginx)
4. [Panduan Instalasi & Menjalankan](#4-panduan-instalasi--menjalankan)
   - [Prasyarat](#41-prasyarat)
   - [Konfigurasi .env](#42-konfigurasi-env)
   - [Membangun & Menjalankan Layanan](#43-membangun--menjalankan-layanan)
   - [Membuat Akun Superadmin](#44-membuat-akun-superadmin)
   - [Akses Aplikasi](#45-akses-aplikasi)
5. [API Reference](#5-api-reference)
6. [Panduan Pengujian](#6-panduan-pengujian)
   - [Unit Testing (Backend)](#61-unit-testing-backend)
   - [Unit Testing (Frontend)](#62-unit-testing-frontend)
   - [Integration Testing](#63-integration-testing)
   - [E2E Testing](#64-e2e-testing)
7. [Changelog](#7-changelog)
8. [Aturan Anti-Bloatware & OpenSearch Mandat](#8-aturan-anti-bloatware--opensearch-mandat)

---

## 1. Ringkasan Proyek

**NOD (Network Observability Dashboard)** adalah aplikasi web enterprise yang mengonsolidasi telemetry jaringan dari tiga sumber OpenSearch ke dalam satu tampilan operasional terpadu:

| Sumber Data | Indeks OpenSearch | Data |
|-------------|-------------------|------|
| AppID Flow | `fortigate-appid-flow-*` | NetFlow/IPFIX dengan DPI FortiGate |
| SNMP/Telegraf | `telegraf-index*` | SD-WAN SLA, HA Resources, SSL VPN |
| IPsec VPN | `ipsec-*` | IPsec tunnel sessions |

**Fitur Utama:** Overview Dashboard (9 panel), Traffic Flow Analytics + Sankey, SD-WAN SLA Monitoring, FortiGate Resource View, Raw Data Table (server-side pagination), Alert Engine (Telegram + Email), Report Generator (PDF/HTML/DOCX), RBAC (4 role), Dark Mode.

---

## 2. Arsitektur Sistem

```
                         INTERNET
                            │
                   ┌────────▼────────┐
                   │  Nginx :80      │  Reverse Proxy (TLS Termination)
                   │  (Alpine)        │
                   └───┬──────────┬──┘
                       │          │
              ┌────────▼──┐  ┌───▼──────────┐
              │ Next.js   │  │  FastAPI      │
              │ :3000     │  │  :8000        │
              │ (App Router)│  │  (Uvicorn)   │
              └───────────┘  └───┬───────────┘
                                 │
                      ┌──────────▼──────────┐
                      │  PostgreSQL 15      │
                      │  :5432              │
                      │  (Application DB)   │
                      └─────────────────────┘

         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
   OpenSearch          OpenSearch         OpenSearch
   10.90.150.108       10.80.150.108      10.90.150.108
   (AppID Flow)        (Telegraf)         (IPsec VPN)
```

---

## 3. Arsitektur Kode

### 3.1 Struktur Direktori Lengkap

```text
network-observability-dashboard/
│
├── docker-compose.yml              # Orkestrasi 4 layanan Docker
├── .env.example                    # Template konfigurasi (committed)
├── .env                            # Konfigurasi rahasia (tidak committed)
├── .gitignore
├── AGENTS.md                       # Aturan AI coding assistant
├── PRD_Doc1.md                     # Dokumen PRD lengkap
├── TESTING_WORKFLOWS.md            # Strategi & workflow pengujian
│
├── nginx/
│   └── nginx.conf                  # Reverse proxy: route / → frontend, /api/ → backend
│
├── backend/
│   ├── Dockerfile                  # Python 3.11-slim + Uvicorn
│   ├── requirements.txt            # Daftar dependensi Python
│   ├── alembic.ini                 # Konfigurasi Alembic
│   │
│   ├── alembic/
│   │   ├── env.py                  # Async migration environment
│   │   └── versions/               # Migration scripts (auto-generated)
│   │
│   ├── app/
│   │   ├── main.py                 # ⭐ Entry point FastAPI — lifespan, middleware, routers
│   │   │
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic Settings — semua env var dengan validasi
│   │   │   ├── security.py         # JWT (encode/decode), bcrypt (hash/verify)
│   │   │   └── logging.py          # Structured JSON logging + rotasi + trace_id
│   │   │
│   │   ├── db/
│   │   │   ├── session.py          # Async SQLAlchemy engine & session factory
│   │   │   └── models.py           # 11 ORM models (User, AlertRule, ReportJob, ...)
│   │   │
│   │   ├── opensearch/             # 🔐 Query builders — Q-01 s/d Q-08 compliant
│   │   │   ├── client.py           # AsyncOpenSearch client pool (1 per endpoint)
│   │   │   ├── appid.py            # fortigate-appid-flow-* queries (FR-02, FR-05)
│   │   │   ├── sdwan.py            # telegraf SD-WAN SLA queries (FR-03)
│   │   │   ├── ha.py               # telegraf ha_member queries (FR-04)
│   │   │   ├── sslvpn.py           # telegraf SSL VPN queries (FR-01 P01-A)
│   │   │   └── ipsec.py            # ipsec-* queries (FR-01 P01-B)
│   │   │
│   │   ├── api/                    # Route handlers FastAPI (1 modul per FR group)
│   │   │   ├── auth.py             # POST /auth/login, /logout, /refresh + JWT deps + RBAC
│   │   │   ├── overview.py         # GET /api/v1/overview (FR-01: 9 panel)
│   │   │   ├── traffic.py          # GET /api/v1/traffic/summary (FR-02: 8 komponen)
│   │   │   ├── sdwan.py            # GET /api/v1/sdwan/sla (FR-03)
│   │   │   ├── resources.py        # GET /api/v1/resources (FR-04)
│   │   │   ├── vpn.py              # GET /api/v1/vpn/ssl, /vpn/ipsec
│   │   │   ├── raw_data.py         # GET /api/v1/traffic/raw (FR-05: search_after)
│   │   │   ├── alerts.py           # CRUD /api/v1/alerts/rules, test rule, alert logs
│   │   │   ├── reports.py          # POST /api/v1/reports/generate, download, distribute
│   │   │   ├── users.py            # CRUD /api/v1/users + change password (FR-06, FR-07)
│   │   │   ├── logs.py             # GET /api/v1/logs/user-activity (superadmin only)
│   │   │   └── notifications.py    # GET/PATCH /api/v1/notifications (FR-10)
│   │   │
│   │   ├── schemas/                # Pydantic request/response models
│   │   │   ├── common.py           # APIResponse[T], ErrorDetail, Meta, PaginationParams
│   │   │   ├── user.py             # LoginRequest, TokenResponse, UserCreate/Read/Update
│   │   │   ├── alert.py            # AlertRuleCreate/Read, AlertTestResult, AlertLogRead
│   │   │   ├── overview.py         # OverviewResponse, DeviceResourceStatus, ...
│   │   │   ├── traffic.py          # TrafficSummaryResponse, RawFlowRecord, SankeyData
│   │   │   ├── sdwan_resource_vpn.py  # SDWANResponse, ResourceResponse, VPN sessions
│   │   │   ├── report.py           # ReportGenerateRequest, ReportJobStatus
│   │   │   └── notification.py     # NotificationRead
│   │   │
│   │   └── services/               # Business logic
│   │       ├── alert_engine.py     # APScheduler — state machine INACTIVE→FIRING→RESOLVED
│   │       ├── report_generator.py # WeasyPrint/PDF, Jinja2/HTML, python-docx/DOCX
│   │       ├── chart_renderer.py   # Matplotlib — server-side PNG charts
│   │       └── notifiers/
│   │           ├── telegram.py     # Telegram Bot API via httpx
│   │           ├── email.py        # Async SMTP via aiosmtplib
│   │           ├── whatsapp.py     # WhatsApp Business Cloud API
│   │           └── discord.py      # Discord Webhook
│   │
│   └── scripts/
│       └── seed_superadmin.py      # Interaktif — membuat akun superadmin pertama
│
├── frontend/
│   ├── Dockerfile                  # Multi-stage: build → production runtime
│   ├── package.json                # Next.js 14, Tremor, TanStack Table, d3-sankey, SWR
│   ├── tsconfig.json
│   ├── next.config.mjs              # API proxy rewrites ke backend
│   ├── tailwind.config.ts          # shadcn/ui CSS variables + dark mode tokens
│   ├── postcss.config.js
│   │
│   ├── public/
│   └── src/
│       ├── app/
│       │   ├── globals.css         # CSS variables light/dark, @media print
│       │   ├── layout.tsx          # Root layout (<html>, metadata)
│       │   ├── page.tsx            # Redirect / → /dashboard/overview
│       │   │
│       │   ├── (auth)/
│       │   │   └── login/
│       │   │       └── page.tsx    # Halaman login + JWT auth flow
│       │   │
│       │   └── dashboard/
│       │       ├── layout.tsx       # Sidebar + top nav + notification bell
│       │       ├── overview/
│       │       │   └── page.tsx     # ⭐ FR-01: 9 panel KPI + timeframe controls
│       │       ├── traffic/
│       │       │   └── page.tsx     # FR-02 stub (Traffic Flow + Sankey)
│       │       ├── sdwan/
│       │       │   └── page.tsx     # FR-03 stub
│       │       ├── resources/
│       │       │   └── page.tsx     # FR-04 stub
│       │       ├── vpn/
│       │       │   └── page.tsx     # VPN sessions stub
│       │       ├── raw-data/
│       │       │   └── page.tsx     # FR-05 stub
│       │       ├── alerts/
│       │       │   └── page.tsx     # FR-08/09 stub
│       │       ├── reports/
│       │       │   └── page.tsx     # FR-12/13 stub
│       │       ├── users/
│       │       │   └── page.tsx     # FR-06 stub
│       │       └── settings/
│       │           └── page.tsx     # FR-07 stub
│       │
│       ├── components/             # (shadcn/ui + Tremor wrappers — extendable)
│       │   ├── ui/
│       │   ├── charts/
│       │   └── panels/
│       │
│       ├── lib/
│       │   ├── api.ts              # SWR fetcher + JWT auto-refresh + error handling
│       │   └── utils.ts            # cn(), formatBytes(), formatMs(), formatNumber()
│       │
│       └── types/
│           └── index.ts            # Semua TypeScript interface (mirror Pydantic schemas)
│
├── reports/
│   └── templates/                  # Jinja2 HTML templates (report generation)
│
└── logs/                           # Host-mounted volume
    ├── access.log                  # JSON — setiap API request
    └── error.log                   # JSON — unhandled exceptions
```

### 3.2 Backend — Python / FastAPI

#### Alur Request

```
HTTP Request
    │
    ▼
Nginx (reverse proxy)
    │
    ▼
FastAPI middleware (trace_id + access log)
    │
    ▼
CORS middleware
    │
    ▼
Route handler (app/api/*.py)
    │
    ├──► JWT dependency (get_current_user) → DB lookup → 401/403 jika tidak valid
    ├──► RBAC dependency (require_role)     → 403 jika role tidak cukup
    │
    ▼
Query builder (app/opensearch/*.py)
    │
    ├──► Q-01: tambahkan @timestamp range filter
    ├──► Q-06: tambahkan exact term filter measurement_name
    ├──► Q-02/03/04/05/07/08: comply semua aturan
    │
    ▼
AsyncOpenSearch client → OpenSearch cluster
    │
    ▼
Pydantic schema mapping (app/schemas/*.py)
    │
    ▼
APIResponse envelope → JSON response
```

#### Database Models (PostgreSQL — 11 tabel)

| Model | Tabel | Fungsi |
|-------|-------|--------|
| `User` | `users` | Akun pengguna + role RBAC |
| `RefreshToken` | `refresh_tokens` | Token refresh JWT (revocable) |
| `AlertRule` | `alert_rules` | Aturan alert yang dikonfigurasi |
| `AlertTemplate` | `alert_templates` | Template notifikasi Jinja2 |
| `AlertLog` | `alert_logs` | Riwayat alert yang fired |
| `AlertState` | `alert_states` | State machine alert (INACTIVE/PENDING/FIRING/RESOLVED) |
| `UserActivityLog` | `user_activity_logs` | Audit trail (superadmin only) |
| `Notification` | `notifications` | In-app notification per user |
| `ReportJob` | `report_jobs` | Status background report generation |
| `UserPreference` | `user_preferences` | Theme, alert notification prefs |
| `UserPinnedWidget` | `user_pinned_widgets` | Pinned dashboard widgets |

#### OpenSearch Query Builders — Kepatuhan Q-Mandates

| Rule | Implementasi di Semua Query Builder |
|------|-------------------------------------|
| **Q-01** | `_time_range(gte_ms, lte_ms)` → `{"range": {"@timestamp": {"gte": ..., "lte": ...}}}` |
| **Q-02** | Semua `terms` agg: `"size": min(size, 500)` (dashboard) / `1000` (report) |
| **Q-03** | Semua document fetch: `"_source": {"includes": [...]}` — tidak pernah `true` |
| **Q-04** | Dashboard/alert: `search_after` dengan tiebreaker `_id` — tidak pernah `scroll` |
| **Q-05** | Semua sum/avg/max/min/cardinality: OpenSearch aggregation, bukan Python |
| **Q-06** | `telegraf-index*`: `{"term": {"measurement_name": "ha_member"}}` — exact, bukan wildcard |
| **Q-07** | Multi-device: `terms` agg pada `tag.device` dengan `top_hits` sub-agg dalam 1 query |
| **Q-08** | `page_size` max 500, `search_after` dengan `[@timestamp, _id]` sort key |

### 3.3 Frontend — Next.js 14+ / TypeScript

#### Tech Stack Spesifik

| Library | Versi | Penggunaan |
|---------|-------|-----------|
| **Next.js** | ≥14.x | App Router, Server Components |
| **React** | 18.3 | Client Components untuk interaktivitas |
| **Tremor** | 3.18 | Analytical charts (AreaChart, BarChart, DonutChart) |
| **TanStack Table** | 8.20 | Virtualized raw data table (FR-05) |
| **d3-sankey** | 0.12 | Sankey diagram saja — bukan full d3 |
| **SWR** | 2.2 | Data fetching + auto-refresh + cache |
| **shadcn/ui** | via Radix | UI primitives (button, input, dialog, sheet, ...) |
| **Tailwind CSS** | 3.4 | Utility-first styling + dark mode |
| **React Hook Form** | 7.52 | Form validation (alert rules, user CRUD) |
| **Zod** | 3.23 | Schema validation (frontend-side) |

#### Alur Autentikasi Frontend

```
1. User akses /dashboard/overview
       │
       ▼
2. Dashboard layout cek access token di localStorage
       │
       ├── Token tidak ada → redirect ke /login
       │
       ▼
3. GET /api/v1/overview (via SWR) dengan header Authorization: Bearer {token}
       │
       ├── 200 OK → render 9 panel dashboard
       ├── 401 → auto-refresh token via POST /auth/refresh (cookie)
       │         ├── Berhasil → retry request
       │         └── Gagal → redirect ke /login
       └── 403 → tampilkan error state
```

#### Komponen Halaman — Status Saat Ini

| Halaman | Path | FR | Status |
|---------|------|-----|--------|
| **Login** | `/login` | Auth | ✅ JWT auth flow full |
| **Overview Dashboard** | `/dashboard/overview` | FR-01 | ✅ 9 panel KPI, timeframe, auto-refresh, FR-14 warning |
| **Traffic Flow** | `/dashboard/traffic` | FR-02 | ✅ Bar chart, donut, Sankey, timeline, top IPs, protocols, egress |
| **SD-WAN SLA** | `/dashboard/sdwan` | FR-03 | ✅ 3 sites × 4 links (WAN/MPLS), latency/jitter/loss charts |
| **Resources** | `/dashboard/resources` | FR-04 | ✅ CPU/Mem/Sessions timeline, device status cards, HA sync |
| **VPN Sessions** | `/dashboard/vpn` | VPN | ✅ SSL + IPsec active user tables with traffic stats |
| **Raw Data** | `/dashboard/raw-data` | FR-05 | ✅ TanStack Table, search_after, filters, CSV export, correlation |
| **Alerts** | `/dashboard/alerts` | FR-08/09 | ✅ Rules CRUD, test rule, enable/disable toggle, history |
| **Reports** | `/dashboard/reports` | FR-12/13 | ✅ Full — type/format selectors, preset+custom time range, 24h warning dialog, generate→poll→download, distribute panel (email/Telegram/Discord/WhatsApp), job history with status polling |
| **Users** | `/dashboard/users` | FR-06 | 🔧 Stub — API backend siap |
| **Settings** | `/dashboard/settings` | FR-07 | 🔧 Stub — API backend siap |

### 3.4 Infrastruktur — Docker Compose / Nginx

#### Layanan

| Layanan | Image | Port Internal | Health Check |
|---------|-------|--------------|-------------|
| **nginx** | `nginx:alpine` | 80 (bound to host) | — |
| **frontend** | Custom build (Node 22) | 3000 | — |
| **backend** | Custom build (Python 3.11) | 8000 | `GET /health` setiap 30s |
| **db** | `postgres:15-alpine` | 5432 | `pg_isready` setiap 10s |

#### Nginx Routing

| Path | Target | Keterangan |
|------|--------|-----------|
| `:80` | → `:443` | **HTTP → HTTPS redirect (301)** |
| `:443` | SSL terminated | Self-signed certificate (production: Let's Encrypt) |
| `/` | `frontend:3000` | Next.js static + SSR |
| `/api/*` | `backend:8000` | FastAPI REST endpoints |
| `/ws/*` | `backend:8000` | WebSocket (alert push) |
| `/auth/*` | `backend:8000` | Login/logout/refresh |
| `/health` | `backend:8000` | Docker health check |
| `/metrics` | `backend:8000` | Prometheus (jika diaktifkan) |

**SSL Certificate:** Self-signed untuk development/review.
Untuk production, ganti dengan Let's Encrypt:
```bash
certbot certonly --webroot -w /usr/share/nginx/html -d nod.example.com
# Lalu update nginx.conf:
#   ssl_certificate     /etc/letsencrypt/live/nod.example.com/fullchain.pem;
#   ssl_certificate_key /etc/letsencrypt/live/nod.example.com/privkey.pem;
```

**HSTS:** `Strict-Transport-Security: max-age=31536000; includeSubDomains`

---

## 4. Panduan Instalasi & Menjalankan

### 4.1 Prasyarat

- **Docker Engine** ≥ 24.x
- **Docker Compose** v2
- Akses jaringan ke OpenSearch clusters (lihat `.env.example`)
- Port 80 tersedia di host (atau ubah `NGINX_PORT`)

### 4.2 Konfigurasi .env

**Langkah 1:** Salin file contoh:

```bash
cp .env.example .env
```

**Langkah 2:** Edit `.env` — variabel **wajib** diisi:

```dotenv
# ──────────────────────────────────────────────────────────────────
# WAJIB: JWT Secret — generate dengan perintah:
#   openssl rand -base64 32
# ──────────────────────────────────────────────────────────────────
JWT_SECRET=GENERATE_DENGAN_OPENSSL_RAND_BASE64_32

# ──────────────────────────────────────────────────────────────────
# WAJIB: Password PostgreSQL
# ──────────────────────────────────────────────────────────────────
POSTGRES_PASSWORD=password_anda_disini

# Sesuaikan DATABASE_URL dengan password yang sama:
DATABASE_URL=postgresql+asyncpg://nod_user:password_anda_disini@db:5432/nod_db

# ──────────────────────────────────────────────────────────────────
# WAJIB: OpenSearch endpoints (sesuaikan dengan environment anda)
# ──────────────────────────────────────────────────────────────────
OPENSEARCH_APPID_URL=https://10.90.150.108:9200
OPENSEARCH_TELEGRAF_URL=https://10.80.150.108:9200
OPENSEARCH_IPSEC_URL=https://10.90.150.108:9200

# ──────────────────────────────────────────────────────────────────
# WAJIB: Site names — daftar measurement_name di telegraf-index*
# ──────────────────────────────────────────────────────────────────
TELEGRAF_SDWAN_SITES=Site_FGT-DC,Site_FGT-DRC,Site_FGT_Office
TELEGRAF_SSLVPN_SITES=Site_FGT-DC_SSLVPN

# ──────────────────────────────────────────────────────────────────
# OPSIONAL: Notifikasi (kosongkan jika tidak digunakan)
# ──────────────────────────────────────────────────────────────────
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=alert@company.com
# SMTP_PASS=app_password
# SMTP_FROM_ADDRESS=alert@company.com
#
# TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234
# TELEGRAM_CHAT_ID=-1001234567890
```

### 4.3 Membangun & Menjalankan Layanan

```bash
# Step 1: Build dan start semua layanan (detached mode)
docker compose up -d --build

# Step 2: Tunggu semua healthy (sekitar 60-120 detik)
docker compose ps
# Semua kolom STATUS harus menunjukkan "healthy" atau "Up"

# Step 3: Jalankan migrasi database (first run only)
docker compose exec backend alembic upgrade head

# Step 4: Buat akun superadmin (first run only)
docker compose exec -it backend python scripts/seed_superadmin.py
```

### 4.4 Membuat Akun Superadmin

Script `seed_superadmin.py` berjalan secara **interaktif**. Gunakan flag `-it`:

```bash
# Interactive (ada terminal TTY):
docker compose exec -it backend python -m scripts.seed_superadmin
```

**Non-interactive** (CI/CD, VM tanpa TTY) — via environment variables:

```bash
docker compose exec -e NOD_SUPERADMIN_USER=admin_noc \
                      -e NOD_SUPERADMIN_PASS=SuperAdmin123! \
                      -e NOD_SUPERADMIN_EMAIL=admin@company.com \
                      backend python -m scripts.seed_superadmin
```

> **Catatan:** Gunakan `python -m scripts.seed_superadmin` (module mode),
> **bukan** `python scripts/seed_superadmin.py` (script mode).
> Script mode menyebabkan `sys.path` mengarah ke `/app/scripts/`
> sehingga module `app` tidak ditemukan.

**Output yang diharapkan:**

```
============================================================
  NOD — Superadmin Account Seeding
============================================================

Superadmin username: admin_noc
Superadmin email: admin@company.com
Superadmin password (min 8 chars): ********
Confirm password: ********
Full name (optional): NOC Administrator

✅ Superadmin account created successfully!
   Username: admin_noc
   Role: superadmin
```

**Catatan penting:**
- Password minimal **8 karakter**
- Username dan email harus **unik** (tidak boleh duplikat)
- Akun superadmin **tidak bisa dihapus atau diubah rolenya**
- Jalankan script ini **sekali saja**. Jika perlu superadmin tambahan, gunakan halaman User Management (admin role)

### 4.5 Akses Aplikasi

| URL | Deskripsi | Role Minimum |
|-----|-----------|-------------|
| `https://localhost/` | Dashboard (redirect ke login jika belum auth) | — |
| `https://localhost/login` | Halaman login | Public |
| `https://localhost/dashboard/overview` | Overview Dashboard (FR-01) | `viewer` |
| `https://localhost/api/docs` | Swagger UI (OpenAPI docs) | Public |
| `https://localhost/api/redoc` | ReDoc (OpenAPI docs) | Public |
| `https://localhost/health` | Health check semua layanan | Public |

> **HTTP otomatis redirect ke HTTPS (301).** Jika menggunakan self-signed certificate,
> browser akan menampilkan peringatan — klik "Advanced" → "Proceed" untuk melanjutkan.

---

## 5. API Reference

Semua endpoint mengembalikan response envelope standar:

```json
{
  "success": true,
  "data": { },
  "meta": { "total": 100, "page": 1, "page_size": 25, "query_took_ms": 47 },
  "error": null
}
```

### Daftar Endpoint Lengkap

| Method | Path | FR | Role Min | Deskripsi |
|--------|------|-----|----------|-----------|
| `POST` | `/auth/login` | — | Public | Login → access token + refresh cookie |
| `POST` | `/auth/logout` | — | Auth | Revoke session + hapus cookie |
| `POST` | `/auth/refresh` | — | Cookie | Refresh access token |
| `GET` | `/health` | — | Public | Health check semua dependensi |
| `GET` | `/api/v1/overview` | FR-01 | `viewer` | 9 panel dashboard dalam 1 response |
| `GET` | `/api/v1/traffic/summary` | FR-02 | `viewer` | Traffic analytics (8 komponen) |
| `GET` | `/api/v1/traffic/raw` | FR-05 | `operator` | Paginated raw flow records (search_after) |
| `GET` | `/api/v1/sdwan/sla` | FR-03 | `viewer` | SD-WAN latency, jitter, packet loss |
| `GET` | `/api/v1/resources` | FR-04 | `viewer` | CPU, memory, sessions per device |
| `GET` | `/api/v1/vpn/ssl` | — | `viewer` | Active SSL VPN users detail |
| `GET` | `/api/v1/vpn/ipsec` | — | `viewer` | Active IPsec VPN users detail |
| `GET` | `/api/v1/alerts/rules` | FR-09 | `admin` | List alert rules |
| `POST` | `/api/v1/alerts/rules` | FR-09 | `admin` | Create alert rule |
| `PUT` | `/api/v1/alerts/rules/{id}` | FR-09 | `admin` | Update alert rule |
| `DELETE` | `/api/v1/alerts/rules/{id}` | FR-09 | `admin` | Delete alert rule |
| `POST` | `/api/v1/alerts/rules/{id}/test` | FR-09 | `admin` | Test rule (no notification) |
| `GET` | `/api/v1/alerts/logs` | FR-11 | `admin` | Alert firing history |
| `GET` | `/api/v1/logs/user-activity` | FR-11 | **`superadmin`** | Audit trail — superadmin only |
| `POST` | `/api/v1/reports/generate` | FR-12 | `operator` | Trigger async report → 202 |
| `GET` | `/api/v1/reports/status/{job_id}` | FR-12 | `operator` | Poll report status |
| `GET` | `/api/v1/reports/download/{job_id}` | FR-12 | `operator` | Download generated report |
| `POST` | `/api/v1/reports/distribute/{job_id}` | FR-13 | `operator` | Distribute report ke channels |
| `GET` | `/api/v1/users` | FR-06 | `admin` | List users |
| `POST` | `/api/v1/users` | FR-06 | `admin` | Create user |
| `PUT` | `/api/v1/users/{id}` | FR-06 | `admin` | Update user |
| `PUT` | `/api/v1/users/me/password` | FR-07 | Auth | Change own password |
| `GET` | `/api/v1/notifications` | FR-10 | Auth | User notifications |
| `PATCH` | `/api/v1/notifications/{id}/read` | FR-10 | Auth | Mark notification read |
| `POST` | `/api/v1/notifications/mark-all-read` | FR-10 | Auth | Mark all read |
| `WS` | `/ws/alerts?token={jwt}` | FR-10 | Auth | Real-time alert push (WebSocket) |

### Role Hierarchy

| Role | Level | Akses |
|------|-------|-------|
| `superadmin` | 3 | Semua, termasuk user activity logs |
| `admin` | 2 | User management, alert rules, alert logs |
| `operator` | 1 | Semua dashboard view, report generation, raw data |
| `viewer` | 0 | Read-only dashboard |

---

### 5.5 Panduan Konfigurasi Alert Rule

Bagian ini menjelaskan field yang tersedia untuk membuat alert rules
melalui halaman **Alerts** (`/dashboard/alerts`) atau langsung via API.

#### 5.5.1 Data Source & Measurement Mapping

| `data_source` | OpenSearch Index | Measurement / Query | Deskripsi |
|---------------|------------------|---------------------|-----------|
| `ha_resource` | `telegraf-index*` | `measurement_name = ha_member` | Resource FortiGate (CPU, memory, sessions, HA sync) |
| `appid_flow` | `fortigate-appid-flow-*` | — (AppID flow records) | Traffic flow (throughput, aplikasi, IP) |
| `sdwan_sla` | `telegraf-index*` | `measurement_name = {site_name}` | SD-WAN SLA (latency, jitter, packet loss) |
| `vpn_ssl` | `telegraf-index*` | `measurement_name = {site}_SSLVPN` | SSL VPN active users |
| `vpn_ipsec` | `ipsec-*` | `ipsec_normalized` (event-driven) | IPsec VPN tunnel sessions |

#### 5.5.2 Metric Fields — `ha_resource`

| `metric_field` | Tipe | Satuan | Contoh Threshold |
|----------------|------|--------|-----------------|
| `ha_member.cpu_usage` | float | persen (%) | `> 80` warning, `> 95` critical |
| `ha_member.mem_usage` | float | persen (%) | `> 85` warning, `> 95` critical |
| `ha_member.session_count` | integer | sessions | `> 50000` high load |
| `ha_member.sync_status` | integer | 0/1 | `== 0` out of sync — critical |

> Alert engine mengambil device **pertama** dari hasil query.
> Untuk alert per-hostname (FG_DC_GTN-01/02), perlu enhancement.

#### 5.5.3 Metric Fields — `appid_flow`

| `metric_field` | Tipe | Satuan |
|----------------|------|--------|
| `total_bytes` | integer | bytes |

> Hanya `total_throughput` yang didukung saat ini.

#### 5.5.4 Metric Fields — `sdwan_sla`

Format: `{site}.{metric}_link{1-4}`

| Metric | Satuan | Contoh Field |
|--------|--------|-------------|
| `latency_link{1-4}` | ms | `Site_FGT-DC.latency_link1` |
| `jitter_link{1-4}` | ms | `Site_FGT-DRC.jitter_link3` |
| `packet_loss_link{1-4}` | persen | `Site_FGT_Office.packet_loss_link2` |

**Site names valid:** `Site_FGT-DC`, `Site_FGT-DRC`, `Site_FGT_Office`

**Link numbers:** 1-4 (2 WAN + 2 MPLS per site)

> **Alert engine belum diimplementasikan** untuk `sdwan_sla`.

#### 5.5.5 Metric Fields — `vpn_ssl` & `vpn_ipsec`

| `data_source` | `metric_field` | Tipe |
|---------------|----------------|------|
| `vpn_ssl` | `active_users` | integer |
| `vpn_ipsec` | `active_users` | integer |
| `vpn_ipsec` | `ipsec_normalized.bytes_in` | integer |
| `vpn_ipsec` | `ipsec_normalized.bytes_out` | integer |
| `vpn_ipsec` | `ipsec_normalized.tunnel_lifetime` | integer (detik) |

> **Alert engine belum diimplementasikan** untuk `vpn_ssl` dan `vpn_ipsec`.

#### 5.5.6 Aggregation & Condition

| `aggregation` | Deskripsi | Cocok untuk |
|---------------|-----------|------------|
| `avg` | Rata-rata metric dalam window | CPU, latency, jitter |
| `max` | Nilai maksimum dalam window | Spike detection |
| `min` | Nilai minimum dalam window | Bandwidth garansi |
| `sum` | Total akumulasi dalam window | Total bytes, sessions |
| `count` | Jumlah dokumen match | Event frequency |

| `condition` | Arti |
|-------------|------|
| `>` | Lebih besar dari threshold |
| `<` | Lebih kecil dari threshold |
| `>=` | Lebih besar atau sama dengan |
| `<=` | Lebih kecil atau sama dengan |
| `==` | Sama dengan (toleransi 0.001) |

#### 5.5.7 State Machine

```
INACTIVE ──(condition met)──→ PENDING
                                  │
                     sustained_for_minutes elapsed?
                                  │
                                  ↓
                              FIRING ──→ notifikasi + WebSocket push
                                  │
                         (condition NOT met)
                                  │
                                  ↓
                             RESOLVED ──→ WebSocket push (resolved)
                                  │
                         (condition met again)
                                  │
                                  ↓
                              PENDING (siklus ulang)
```

**Parameter:**
- `evaluation_window_minutes` (1–1440): jendela query OpenSearch
- `sustained_for_minutes` (0 = langsung): durasi sebelum FIRING
- `ALERT_POLL_INTERVAL_SECONDS` (default 60s): interval scheduler
- `ALERT_RENOTIFY_INTERVAL_MINUTES` (default 30m): re-notifikasi saat FIRING

#### 5.5.8 Contoh Alert Rules

**CPU Critical:**
```json
{
  "name": "CPU Usage Critical",
  "severity": "CRITICAL",
  "data_source": "ha_resource",
  "metric_field": "ha_member.cpu_usage",
  "aggregation": "avg",
  "condition": ">",
  "threshold_value": 95,
  "evaluation_window_minutes": 5,
  "sustained_for_minutes": 3,
  "notify_channels": ["telegram", "email"]
}
```

**HA Out of Sync:**
```json
{
  "name": "HA Out of Sync",
  "severity": "CRITICAL",
  "data_source": "ha_resource",
  "metric_field": "ha_member.sync_status",
  "aggregation": "avg",
  "condition": "==",
  "threshold_value": 0,
  "evaluation_window_minutes": 1,
  "sustained_for_minutes": 0,
  "notify_channels": ["telegram", "email"]
}
```

**Throughput Drop:**
```json
{
  "name": "Throughput Below 10 Mbps",
  "severity": "WARNING",
  "data_source": "appid_flow",
  "metric_field": "total_bytes",
  "aggregation": "sum",
  "condition": "<",
  "threshold_value": 1310720,
  "evaluation_window_minutes": 5,
  "sustained_for_minutes": 10,
  "notify_channels": ["email"]
}
```

#### 5.5.9 Notification Channels

| Channel | Konfigurasi `.env` |
|---------|-------------------|
| `email` | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` |
| `telegram` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `discord` | `DISCORD_WEBHOOK_URL` |
| `whatsapp` | `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` |

> Saat ini `_notify()` hanya dispatch `email` + `telegram`.

#### 5.5.10 Test Rule & WebSocket

- **Test:** `POST /api/v1/alerts/rules/{id}/test` — evaluasi tanpa notifikasi
- **WebSocket:** `wss://localhost/ws/alerts?token={jwt}` — push FIRING/RESOLVED real-time

---

## 6. Panduan Pengujian

> **Referensi utama:** `TESTING_WORKFLOWS.md` di root proyek.
> Semua workflow pengujian harus mengikuti dokumen tersebut.

### 6.1 Unit Testing (Backend)

**Framework:** Pytest + pytest-asyncio

```bash
# Install test dependencies
cd backend
pip install pytest pytest-asyncio httpx

# Jalankan unit tests
python -m pytest tests/unit/ -v

# Dengan coverage
python -m pytest tests/unit/ -v --cov=app --cov-report=html
```

**Area yang di-test:**
- `app/opensearch/*.py` — query builders menghasilkan DSL JSON yang benar
- `app/core/security.py` — JWT encode/decode, bcrypt hash/verify
- `app/schemas/*.py` — Pydantic validation (field constraints, enum values)
- `app/services/alert_engine.py` — State machine transitions (INACTIVE → PENDING → FIRING → RESOLVED)

### 6.2 Unit Testing (Frontend)

**Framework:** Vitest + React Testing Library (belum di-setup — perlu `npm install -D vitest @testing-library/react`)

```bash
cd frontend
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
npx vitest run
```

**Area yang di-test:**
- `lib/utils.ts` — `formatBytes()`, `formatMs()`, `formatNumber()`, `cn()`
- `lib/api.ts` — SWR fetcher, JWT auto-refresh logic
- Custom hooks (timeframe calculator)

### 6.3 Integration Testing

**Framework:** Pytest + httpx (AsyncClient) + testcontainers (PostgreSQL)

```bash
cd backend
pip install testcontainers pytest-httpx

# Jalankan integration tests (butuh Docker untuk testcontainers)
python -m pytest tests/integration/ -v
```

**Workflow yang di-test:**
1. **Auth flow:** Login → dapatkan JWT → akses protected route → refresh → logout
2. **RBAC enforcement (AT-06, AT-07):** `viewer` → `GET /api/v1/users` → HTTP 403
3. **Report generation (AT-12):** Trigger report → poll status → verify file
4. **OpenSearch mocking:** Mock `AsyncOpenSearch` — verifikasi Q-01 (`@timestamp` bounds), Q-06 (exact `measurement_name`)

### 6.4 E2E Testing

**Framework:** Playwright (untuk Next.js)

```bash
cd frontend
npx playwright install
npx playwright test
```

**Critical workflows (AT-01 s/d AT-18):**
- Login → Overview Dashboard load dalam 3 detik → 9 panel render
- Custom timeframe > 24h → warning dialog muncul (AT-13)
- Raw Data Table → filter → URL update → page 2 → data update (AT-05)
- Alert rule → create → test rule → no notification sent (AT-09)
- Dark mode toggle → no FOUC, semua chart render benar (AT-16)

---

## 7. Changelog

| Versi | Tanggal | Perubahan |
|-------|---------|-----------|
| **1.1.0** | 2026-06-04 | **Frontend completion + bug fixes.** |
| | | **Frontend:** Traffic Flow page (FR-02: bar chart, donut, Sankey, timeline, top IPs, protocols, egress), SD-WAN SLA page (FR-03: 3 sites × 4 links WAN/MPLS, latency/jitter/loss per type), Resources page (FR-04: CPU/Mem/Sessions per device, HA sync badges, device selector dropdown), Raw Data Table (FR-05: TanStack Table, search_after pagination, filter panel, CSV export, column toggle, correlation_id + direction), Alerts page (FR-08/09: rules CRUD, test rule, enable/disable toggle, alert history), VPN Sessions page (SSL + IPsec tables), **Reports page (FR-12/13: type selector, format selector, preset/custom time range, 24h warning dialog, generate→poll→download flow, distribute panel with email/Telegram/Discord/WhatsApp channels, job history table with status polling)**, auth gating on all pages (token check before SWR fetch), layout auth gate with loading state. Overview resource panels upgraded: SVG Gauge cards (P01-C CPU, P01-D Memory), Sparkline card (P01-E Sessions), Status Badge (P01-F HA Sync). 2-device HA display with hostname-level grouping (FG_DC_GTN-01 primary, FG_DC_GTN-02 secondary). |
| | | **Backend:** SD-WAN refactored to 4-link per site with WAN/MPLS labels + endpoint routing (DRC on appid endpoint), app-0 filter on all AppID queries, ingress/egress fields changed to netif.alias, Sankey WAN→LAN direction fix (exclude return-path), correlation_id + correlation_direction fields in raw_flows, IPsec query builder reverted to PRD spec (ipsec_normalized), WebSocket (/ws/alerts) endpoint + manager + alert engine broadcast, HTTPS/TLS skip verify, refresh cookie secure=auto-detect + samesite=lax. **Report export engine (FR-12): HTML/PDF/DOCX generation via WeasyPrint+Jinja2+python-docx, Matplotlib chart rendering with epoch→datetime x-axis formatting, VPN bar chart, CSS @page footer, background task via asyncio.create_task, report list endpoint. Report distribution (FR-13): 4-channel dispatch (email+attachment via aiosmtplib, Telegram sendDocument, Discord webhook file, WhatsApp media upload) wired from stub to production.** HA query aggregation changed from tag.device → tag.hostname (2 devices visible). Session sparkline query added to ha.py for overview P01-E. |
| | | **Bug fixes (18):** frontend Docker build (npm ci→npm install), version obsolete warning, Radix UI versions, next.config.ts→mjs, Docker COPY glob, libgdk package, postcss CJS/ESM, pydantic email-validator, health check lenient, alembic migration generation, next start vs standalone, HTTPS+SSL+TLS verify, text field .keyword fixes (tag.username, measurement_name, tag.remote_gw_ip, tag.device), bcrypt/passlib compat warning, Site_FGT_Office site name config, Site_FGT-DRC endpoint routing, report DB transaction commit before background task spawn. |
| **1.0.0** | 2026-06-03 | **Initial release.** Seluruh fondasi proyek selesai dibangun. |
| | | **Backend:** FastAPI app, 11 ORM models, 5 OpenSearch query builders (Q-01–Q-08 compliant), 11 API route modules, RBAC middleware (4 role), JWT auth flow (access + refresh token), alert engine (APScheduler state machine), report generator (PDF/HTML/DOCX via WeasyPrint/Jinja2/python-docx), chart renderer (Matplotlib), 4 notifier modules (Telegram, Email, WhatsApp, Discord), Alembic migrations, superadmin seed script, structured JSON logging dengan rotasi + trace_id. |
| | | **Frontend:** Next.js 14+ App Router scaffolding, shadcn/ui CSS variables (light/dark mode), Tailwind CSS, SWR API client dengan JWT auto-refresh, TypeScript types (mirror Pydantic schemas), Login page (full JWT auth flow), Dashboard layout (sidebar + top nav + notification bell), Overview Dashboard page (FR-01: 9 panel KPI, timeframe selector, FR-14 warning dialog, auto-refresh), 8 halaman stub siap dikembangkan. |
| | | **Infrastruktur:** docker-compose.yml (4 services: nginx, frontend, backend, db), Nginx reverse proxy config, Dockerfiles (multi-stage untuk frontend), .env.example lengkap, .gitignore. |
| | | **Kepatuhan:** Semua Q-01 s/d Q-08 mandates diterapkan di setiap query builder. Anti-bloatware policy enforced: tidak ada dead code, tidak ada sync OpenSearch call, tidak ada raw `_source` ke frontend, tidak ada N+1 query, tidak ada import tidak terpakai. |

---

## 8. Aturan Anti-Bloatware & OpenSearch Mandat

> **⚠️ WAJIB DIPATUHI — Lihat `AGENTS.md` dan PRD Section 8 untuk detail lengkap.**

### Ringkasan Aturan

| # | Aturan | Deskripsi Singkat |
|---|--------|-------------------|
| Q-01 | Time Bounds | Setiap query HARUS punya `range` filter `@timestamp` dengan `gte` dan `lte` |
| Q-02 | Explicit Size | Semua `terms` aggregation HARUS punya `size` eksplisit (max 500 UI, 1000 report) |
| Q-03 | Source Filtering | `_source: {"includes": [...]}` — tidak pernah `_source: true` |
| Q-04 | No Scroll API | Dashboard/alert pakai `search_after`, bukan `scroll` |
| Q-05 | Aggregation | Semua sum/avg/max/min dijalankan di OpenSearch, bukan di Python/JS |
| Q-06 | Exact measurement_name | `telegraf-index*` query HARUS pakai exact `term` filter, BUKAN wildcard |
| Q-07 | No N+1 Queries | Tidak boleh `for ... in: await es.search()` — pakai `terms` agg atau `multi_search` |
| Q-08 | Pagination Cap | `from + size` ≤ 10,000 — gunakan `search_after` untuk halaman lebih dalam |

### Dependency Budget

- **Backend:** Tidak boleh menambah package ke `requirements.txt` tanpa justifikasi PR documented
- **Frontend:** Tidak boleh menambah npm package > 50 KB (gzip) tanpa justifikasi
- **Infrastruktur:** Tidak boleh menambah Docker service (Redis, RabbitMQ, Celery, dll) tanpa architecture review

---

**Lisensi:** Internal — Confidential  
**Kontak:** NOC Engineering Team  
**Repositori:** `network-observability-dashboard`
