"""
FastAPI application entry point.
Initializes middleware, routers, scheduler, and structured logging.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.db.session import AsyncSessionLocal
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import (
    alerts,
    auth,
    ha,
    interface_stats,
    logs,
    notifications,
    overview,
    raw_data,
    reports,
    resources,
    sdwan,
    traffic_flow,
    traffic_inbound,
    traffic_internal,
    users,
    vpn,
)
from app.api.config.maintenance import router as config_maintenance_router
from app.api.config.notifications import router as config_notifications_router
from app.api.config.notification_templates import router as config_notification_templates_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.limiter import limiter, get_real_client_ip
from app.db.session import engine

logger = logging.getLogger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    import logging
    logger = logging.getLogger(__name__)
    logger.info("NOD Backend starting up")

    # Start alert scheduler (FR-08)
    from app.services.alert_engine import start_alert_scheduler
    start_alert_scheduler()

    # Start token cleanup (hourly, deletes expired/revoked tokens older than 24h)
    from app.services.token_cleanup import start_token_cleanup_scheduler
    start_token_cleanup_scheduler()

    # Start report schedule checker (P8)
    from app.services.report_scheduler import start_report_scheduler
    start_report_scheduler()
    # Reset any pending/running ReportJob records from a previous crash/restart (Fix 2.2)
    from app.db.models import ReportJob
    from sqlalchemy import update
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(ReportJob)
            .where(ReportJob.status.in_(["pending", "running"]))
            .values(status="failed", error_message="Server restarted")
        )
        await session.commit()
    logger.info("Reset pending/running report jobs from previous session")

    # Auto-cleanup expired reports on startup
    from app.api.reports import _cleanup_expired_reports
    async with AsyncSessionLocal() as session:
        cleaned = await _cleanup_expired_reports(session)
        if cleaned:
            logger.info(f"Cleaned up {cleaned} expired reports on startup")

    # Seed initial 6 alert templates (v3 §3.12)
    from app.services.template_seeder import seed_alert_templates, seed_field_catalog
    seeded = await seed_alert_templates()
    if seeded:
        logger.info(f"Seeded {seeded} alert templates")

    # Seed field catalog (§11.2)
    seeded_cat = await seed_field_catalog()
    if seeded_cat:
        logger.info(f"Seeded {seeded_cat} field catalog rows")

    # Seed default notification template (§11.1)
    from app.services.template_seeder import seed_notification_templates
    seeded_nt = await seed_notification_templates()
    if seeded_nt:
        logger.info(f"Seeded {seeded_nt} notification templates")

    # DB connection pool is lazily initialized by SQLAlchemy
    yield
    # Shutdown
    from app.services.alert_engine import scheduler as alert_scheduler
    alert_scheduler.shutdown(wait=False)
    from app.services.report_scheduler import scheduler as report_scheduler
    report_scheduler.shutdown(wait=False)
    from app.services.report_generator import chart_executor
    chart_executor.shutdown(wait=False)
    await engine.dispose()
    logger.info("NOD Backend shut down")


# ─────────────────────────────────────────────────────────────────
# App instantiation
# ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="NOD — Network Observability Dashboard",
    version="1.0.0",
    docs_url="/api/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/api/redoc" if settings.ENVIRONMENT == "development" else None,
    openapi_url="/api/openapi.json" if settings.ENVIRONMENT == "development" else None,
    lifespan=lifespan,
)

# ── Rate Limiting (P0 security) ───────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Trace-ID"],
)


# ── Security Headers (P0 — Fix 1.6) ──────────────────────────
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: Callable):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ─────────────────────────────────────────────────────────────────
# Middleware: Trace ID + Access Logging
# ─────────────────────────────────────────────────────────────────

@app.middleware("http")
async def trace_and_log_middleware(request: Request, call_next: Callable):
    import logging
    logger = logging.getLogger("access")
    trace_id = request.headers.get("X-Trace-ID", uuid.uuid4().hex)
    request.state.trace_id = trace_id

    start_time = time.monotonic()
    response: Response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    logger.info(
        "request",
        extra={
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "client_ip": get_real_client_ip(request),
        },
    )

    response.headers["X-Trace-ID"] = trace_id
    return response


# ─────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(overview.router)
app.include_router(traffic_flow.router)
app.include_router(traffic_inbound.router)
app.include_router(traffic_internal.router)
app.include_router(sdwan.router)
app.include_router(ha.router)
app.include_router(interface_stats.router)
app.include_router(resources.router)
app.include_router(vpn.router)
app.include_router(raw_data.router)
app.include_router(alerts.router)
app.include_router(reports.router)
app.include_router(users.router)
app.include_router(logs.router)
app.include_router(notifications.router)
app.include_router(config_notifications_router)
app.include_router(config_maintenance_router)
app.include_router(config_notification_templates_router)


# ─────────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Docker health check endpoint."""
    from app.opensearch.client import check_all_clusters
    from sqlalchemy import text
    from app.db.session import AsyncSessionLocal

    status = {
        "api": "ok",
        "db": "ok",
        "opensearch_dc": "ok",
        "opensearch_drc": "ok",
        "opensearch_ipsec": "ok",
    }

    # Check DB — only this is critical for health
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        status["db"] = "error"

    # Check OpenSearch clusters — non-fatal, log only. Bounded so unreachable
    # clusters (each ping can block ~30s) can't stall /health past the Docker
    # healthcheck timeout and wedge the whole stack as "unhealthy".
    try:
        cluster_status = await asyncio.wait_for(check_all_clusters(), timeout=5)
        for key, is_ok in cluster_status.items():
            status[key] = "ok" if is_ok else "unreachable"
    except Exception:
        for key in ["opensearch_dc", "opensearch_drc", "opensearch_ipsec"]:
            status[key] = "unreachable"

    # Healthy as long as DB is ok (OpenSearch is external dependency)
    if status["db"] != "ok":
        return JSONResponse(content=status, status_code=503)
    return JSONResponse(content=status, status_code=200)




# ─────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "NOD Backend", "version": "1.0.0"}
