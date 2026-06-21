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
docker compose ps

# 6. Run database migration (first time only)
docker compose exec backend alembic upgrade head

# 7. Create superadmin account (first time only, interactive)
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

## Security Features

| Feature | Implementation |
|---------|----------------|
| Rate Limiting | slowapi — 10 req/min on login, 30 req/min on refresh |
| WebSocket Auth | Message-based JWT (not query param) |
| Refresh Rotation | New JWT pair on every /auth/refresh call |
| CSRF Protection | `__Host-` cookie prefix + SameSite=Strict |
| JWT Secret | ≥32 chars enforced at startup |
| Role Guard | WebSocket alerts restricted to admin+ |

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
# {"api":"ok","db":"ok","opensearch_dc":"ok","opensearch_drc":"ok"}
```

---

**License:** Internal — Confidential
**Contact:** NOC Engineering Team
**Repository:** https://github.com/ervindaprtma/NOD-Project
