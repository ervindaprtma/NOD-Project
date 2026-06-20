# NOD — Network Observability Dashboard

**Enterprise-grade FortiGate network observability platform for NOC teams.**

---

## Deploy

### Prerequisites

- Docker Engine ≥ 24.x
- Docker Compose v2
- Network access to OpenSearch clusters
- Port 80/443 available on host

### First-Time Setup

```bash
# 1. Clone repository
git clone https://github.com/ervindaprtma/NOD-Project.git
cd NOD-Project

# 2. Configure environment
cp .env.example .env
# Edit .env — set JWT_SECRET, POSTGRES_PASSWORD, OpenSearch endpoints

# 3. Build and start all services
docker compose up -d --build

# 4. Wait for healthy status
docker compose ps

# 5. Run database migration (first time only)
docker compose exec backend alembic upgrade head

# 6. Create superadmin account (first time only)
docker compose exec -it backend python -m scripts.seed_superadmin
```

### Environment Variables (.env)

**Required:**

| Variable | Description | Example |
|----------|-------------|---------|
| `JWT_SECRET` | JWT signing key | `openssl rand -base64 32` |
| `POSTGRES_PASSWORD` | Database password | `your_password_here` |
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://nod_user:pass@db:5432/nod_db` |
| `OPENSEARCH_DC_URL` | DC OpenSearch endpoint | `http://10.80.150.108:9200` |
| `OPENSEARCH_DRC_URL` | DRC OpenSearch endpoint | `http://10.90.150.108:9200` |

**Optional (Notifications):**

| Variable | Description |
|----------|-------------|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | Email alerts |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts |
| `DISCORD_WEBHOOK_URL` | Discord alerts |

### Access

| URL | Description |
|-----|-------------|
| `https://localhost/` | Dashboard (auto-redirect to login) |
| `https://localhost/login` | Login page |
| `https://localhost/api/docs` | Swagger UI |
| `https://localhost/health` | Health check |

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

```bash
# Run pending migrations
docker compose exec backend alembic upgrade head

# Check current migration version
docker compose exec backend alembic current

# Create new migration (after model changes)
docker compose exec backend alembic revision --autogenerate -m "description"
```

### Backup & Restore

```bash
# Backup database
docker compose exec db pg_dump -U nod_user nod_db > backup_$(date +%Y%m%d).sql

# Restore database
cat backup_20260620.sql | docker compose exec -T db psql -U nod_user nod_db
```

### SSL Certificate

Default: self-signed (browser warning — click "Advanced" → "Proceed").

For production with Let's Encrypt:
```bash
certbot certonly --webroot -w /usr/share/nginx/html -d nod.example.com
# Update nginx.conf with certificate paths, then restart nginx
docker compose restart nginx
```

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

# Search by trace ID
docker compose exec backend grep '"trace_id":"abc123"' logs/access.log
```

### Log Format

Logs are JSON-formatted with these fields:
- `timestamp` — when the event occurred
- `level` — INFO, WARNING, ERROR
- `trace_id` — request correlation ID (from `X-Trace-ID` header)
- `method`, `path`, `status`, `elapsed_ms` — request details
- `message` — log message

### Docker Health Status

```bash
# Check if all services are healthy
docker compose ps

# Backend health endpoint
curl -k https://localhost/health

# Expected response:
# {"api":"ok","db":"ok","opensearch_dc":"ok","opensearch_drc":"ok"}
```

### Performance Checks

```bash
# Backend response times (from access log)
docker compose exec backend tail -100 logs/access.log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        if 'elapsed_ms' in d:
            print(f\"{d['path']}  {d['elapsed_ms']}ms  {d['status']}\")
    except: pass
"

# OpenSearch query times (from API responses)
# Check the 'query_took_ms' field in API responses
curl -s -k -H "Authorization: Bearer TOKEN" https://localhost/api/v1/overview | python3 -m json.tool | grep query_took_ms
```

---

**License:** Internal — Confidential
**Contact:** NOC Engineering Team
**Repository:** https://github.com/ervindaprtma/NOD-Project
