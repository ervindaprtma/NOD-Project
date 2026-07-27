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
- Network access to OpenSearch clusters (DC: 10.80.150.108, DRC: 10.90.150.108)
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

### Environment Variables (.env)

**Required:**

| Variable | Description | Example |
|----------|-------------|---------|
| `JWT_SECRET` | JWT signing key (≥32 chars) | `openssl rand -base64 32` |
| `POSTGRES_PASSWORD` | Database password | `your_password_here` |
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://nod_user:pass@db:5432/nod_db` |
| `OPENSEARCH_DC_URL` | DC OpenSearch (Site_FGT-DC) | `https://10.80.150.108:9200` |
| `OPENSEARCH_DRC_URL` | DRC OpenSearch (Site_FGT-DRC + Site_FGT_Office) | `https://10.90.150.108:9200` |
| `OPENSEARCH_IPSEC_URL` | IPsec OpenSearch | `https://10.90.150.108:9200` |

**Optional (Notifications):**

| Variable | Description |
|----------|-------------|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | Email alerts |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts |
| `DISCORD_WEBHOOK_URL` | Discord alerts |

### Access

| URL | Description |
|-----|-------------|
| `https://nod.esign.id/` | Dashboard (auto-redirect to login) |
| `https://nod.esign.id/login` | Login page |
| `https://nod.esign.id/api/docs` | Swagger UI |
| `https://nod.esign.id/health` | Health check |

---

## Production Deployment (Nginx + Domain)

### Nginx Reverse Proxy

The nginx container terminates SSL and proxies to frontend/backend:

```nginx
upstream frontend_upstream { server frontend:3000; }
upstream backend_upstream { server backend:8000; }

server {
    listen 80;
    server_name nod.esign.id;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name nod.esign.id;

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
  -subj "/CN=nod.esign.id"

# Option 2: Let's Encrypt (production)
certbot certonly --webroot -w /usr/share/nginx/html -d nod.esign.id
# Update nginx.conf with certificate paths, then restart nginx
docker compose restart nginx
```

### Domain Configuration

1. Create DNS A record: `nod.esign.id` → Nginx server IP
2. Ensure Nginx server has ports 80/443 open
3. Update `.env` with production values:
   ```
   NEXT_PUBLIC_API_BASE_URL=https://nod.esign.id
   ALLOWED_ORIGINS=https://nod.esign.id
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
- Same domain (`nod.esign.id`) **+** same `JWT_SECRET` → active sessions survive
  (the refresh cookie still validates). Different domain → users simply re-login;
  no stored data is lost either way.
- Point DNS at the new host and confirm it can reach the OpenSearch clusters
  (`10.80.150.108`, `10.90.150.108`) before going live.

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
curl -k https://nod.esign.id/health

# Expected response:
# {"api":"ok","db":"ok","opensearch_dc":"ok","opensearch_drc":"ok","opensearch_ipsec":"ok"}
```

---

**License:** Internal — Confidential
**Contact:** NOC Engineering Team
**Repository:** https://github.com/ervindaprtma/NOD-Project
