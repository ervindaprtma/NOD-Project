"""System Health status — admin-only aggregate for the Settings → System Health tab.

Read-only. Reuses the endpoint watchdog's live state, a DB ping, the alert scheduler's
job table, and the System Logs queue counters. The recent-events list is NOT here — the
tab pulls it straight from /api/v1/logs/system?event=endpoint_down,endpoint_recovered.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.auth import require_role
from app.db.session import AsyncSessionLocal
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/v1/health", tags=["Health"])


@router.get("/status")
async def health_status(current_user=Depends(require_role("admin"))):
    """Aggregate health for the System Health tab: DB, OpenSearch clusters (watchdog),
    background schedulers, and the log-sink queue."""
    from app.services.endpoint_watchdog import get_state
    from app.services.system_logger import queue_stats

    # DB — the one hard dependency
    db_ok = True
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    # Background schedulers (alert engine, watchdog, token cleanup, report scheduler)
    schedulers: list[dict] = []
    try:
        from app.services.alert_engine import scheduler
        running = scheduler.running
        for job in scheduler.get_jobs():
            nrt = getattr(job, "next_run_time", None)
            schedulers.append({
                "id": job.id,
                "running": running,
                "next_run": nrt.isoformat() if nrt else None,
            })
    except Exception:
        pass

    wd = get_state()
    return APIResponse.ok(data={
        "api": "ok",
        "db": "ok" if db_ok else "error",
        "sources": wd["sources"],
        "watchdog_started_at": wd["started_at"],
        "schedulers": schedulers,
        "log_queue": queue_stats(),
    })
