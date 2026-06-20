"""
FastAPI application entry point.
Initializes middleware, routers, scheduler, and structured logging.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
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
    traffic,
    traffic_flow,
    traffic_inbound,
    traffic_internal,
    users,
    vpn,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.limiter import limiter
from app.db.session import engine, AsyncSessionLocal
from app.db.models import User
from app.opensearch.client import check_all_clusters

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
    docs_url="/api/docs",
    redoc_url="/api/redoc",
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
            "client_ip": request.client.host if request.client else "unknown",
        },
    )

    response.headers["X-Trace-ID"] = trace_id
    return response


# ─────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(overview.router)
app.include_router(traffic.router)
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

    # Check OpenSearch clusters — non-fatal, log only
    try:
        cluster_status = await check_all_clusters()
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
# WebSocket: Real-Time Alerts (FR-10)
# ─────────────────────────────────────────────────────────────────

from app.services.websocket_manager import alert_ws_manager


async def ws_get_current_user(token: str = Query(...)) -> str:
    """Extract user_id from JWT for WebSocket upgrade. Rejects if invalid."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token: missing sub")
        return user_id
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


@app.websocket("/ws/alerts")
async def ws_alerts(
    ws: WebSocket,
    token: str = Query(...),
):
    """
    FR-10: WebSocket endpoint for real-time alert push.
    Requires JWT token as query parameter for authentication.
    Broadcasts FIRING and RESOLVED alert state transitions.
    """
    user_id = await ws_get_current_user(token)
    await alert_ws_manager.connect(ws, user_id)
    try:
        # Keep connection alive; handle incoming messages (heartbeat / ack)
        while True:
            try:
                data = await ws.receive_text()
                # Client can send ping/pong or mark-as-read events
                msg = json.loads(data)
                action = msg.get("action")
                if action == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
    except WebSocketDisconnect:
        await alert_ws_manager.disconnect(user_id)
    except Exception:
        await alert_ws_manager.disconnect(user_id)


# ─────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "NOD Backend", "version": "1.0.0"}
