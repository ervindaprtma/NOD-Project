"""
Logs API.
 - /user-activity : superadmin-only user audit (FR-11, unchanged).
 - /system*       : admin System Logs console (LOGGING_SYSTEM_DESIGN §5).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, require_role
from app.core.limiter import get_real_client_ip, limiter
from app.db.models import SystemLog, User, UserActivityLog
from app.db.session import get_db
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/v1/logs", tags=["Logs"])


@router.get("/user-activity", response_model=APIResponse[list[dict]])
async def get_user_activity_logs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("superadmin")),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str | None = Query(default=None),
):
    """FR-11: User activity audit log. ONLY superadmin can access this endpoint."""
    query = select(UserActivityLog).order_by(UserActivityLog.timestamp.desc())
    if user_id:
        query = query.where(UserActivityLog.user_id == user_id)

    result = await db.execute(query.offset(offset).limit(limit))
    logs = result.scalars().all()

    user_ids = list({log.user_id for log in logs})
    users_map: dict[str, tuple[str, str]] = {}
    if user_ids:
        user_result = await db.execute(
            select(User.id, User.username, User.role).where(User.id.in_(user_ids))
        )
        for row in user_result:
            users_map[row[0]] = (row[1], row[2])

    total = (await db.execute(select(func.count(UserActivityLog.id)))).scalar() or 0

    data = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "username": users_map.get(log.user_id, ("unknown",))[0],
            "role": users_map.get(log.user_id, ("unknown", "unknown"))[1],
            "action": log.action,
            "source_ip": log.source_ip,
            "details": log.details,
            "timestamp": log.timestamp.isoformat(),
        }
        for log in logs
    ]
    return APIResponse.ok(data=data, meta={"total": total})


# ── System Logs console (admin + superadmin) ─────────────────────

def _split(v: Optional[str]) -> list[str]:
    """Comma-separated multi-value → list (the convention the traffic filters use)."""
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


def _filters(*, level, category, source, q, username, event, rule_id, trace_id, from_, to):
    conds = []
    if (levels := _split(level)):
        conds.append(SystemLog.level.in_(levels))
    if (cats := _split(category)):
        conds.append(SystemLog.category.in_(cats))
    if source in ("backend", "frontend"):
        conds.append(SystemLog.source == source)
    if username:
        conds.append(SystemLog.username == username)
    if (events := _split(event)):
        conds.append(SystemLog.event.in_(events))
    if rule_id:
        conds.append(SystemLog.rule_id == rule_id)
    if trace_id:
        conds.append(SystemLog.trace_id == trace_id)
    if from_:
        conds.append(SystemLog.ts >= from_)
    if to:
        conds.append(SystemLog.ts <= to)
    if q:
        like = f"%{q}%"
        conds.append(or_(
            SystemLog.message.ilike(like), SystemLog.event.ilike(like),
            SystemLog.path.ilike(like), SystemLog.username.ilike(like),
        ))
    return conds


def _row_to_dict(r: SystemLog) -> dict:
    return {
        "id": r.id, "ts": r.ts.isoformat(), "level": r.level, "category": r.category,
        "source": r.source, "event": r.event, "message": r.message, "username": r.username,
        "user_id": r.user_id, "source_ip": r.source_ip, "trace_id": r.trace_id,
        "rule_id": r.rule_id, "method": r.method, "path": r.path,
        "status_code": r.status_code, "duration_ms": r.duration_ms, "details": r.details,
    }


@router.get("/system")
async def get_system_logs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
    level: Optional[str] = Query(None, description="INFO|ALERT|ERROR|WARNING (comma-multi)"),
    category: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Filtered, paginated system-log feed + per-level facet counts."""
    conds = _filters(level=level, category=category, source=source, q=q, username=username,
                     event=event, rule_id=rule_id, trace_id=trace_id, from_=from_, to=to)
    base = select(SystemLog)
    count_q = select(func.count()).select_from(SystemLog)
    if conds:
        base = base.where(and_(*conds))
        count_q = count_q.where(and_(*conds))
    total = (await db.execute(count_q)).scalar() or 0
    rows = (await db.execute(
        base.order_by(SystemLog.ts.desc()).offset(offset).limit(limit)
    )).scalars().all()

    # Facets: per-level counts respecting the NON-level filters, so the tab badges
    # stay truthful under the other active filters.
    fconds = _filters(level=None, category=category, source=source, q=q, username=username,
                      event=event, rule_id=rule_id, trace_id=trace_id, from_=from_, to=to)
    fq = select(SystemLog.level, func.count()).group_by(SystemLog.level)
    if fconds:
        fq = fq.where(and_(*fconds))
    facets = {row[0]: row[1] for row in (await db.execute(fq)).all()}

    return APIResponse.ok(data={
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "facets": {"level": facets},
    })


@router.get("/system/stats")
async def system_log_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None, alias="to"),
):
    """Per-level counts for the tab badges + writer queue health."""
    conds = _filters(level=None, category=category, source=source, q=None, username=None,
                     event=None, rule_id=None, trace_id=None, from_=from_, to=to)
    fq = select(SystemLog.level, func.count()).group_by(SystemLog.level)
    if conds:
        fq = fq.where(and_(*conds))
    by_level = {row[0]: row[1] for row in (await db.execute(fq)).all()}

    from app.services.system_logger import get_logger_stats
    return APIResponse.ok(data={"by_level": by_level, "writer": get_logger_stats()})


@router.get("/system/export")
async def export_system_logs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
    level: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(10_000, ge=1, le=50_000),
):
    """CSV of the current filter (already-redacted rows — nothing rehydrated)."""
    conds = _filters(level=level, category=category, source=source, q=q, username=username,
                     event=event, rule_id=rule_id, trace_id=trace_id, from_=from_, to=to)
    base = select(SystemLog)
    if conds:
        base = base.where(and_(*conds))
    rows = (await db.execute(base.order_by(SystemLog.ts.desc()).limit(limit))).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "level", "category", "source", "event", "message",
                "username", "source_ip", "trace_id", "rule_id", "method", "path",
                "status_code", "duration_ms"])
    for r in rows:
        w.writerow([r.ts.isoformat(), r.level, r.category, r.source, r.event, r.message,
                    r.username or "", r.source_ip or "", r.trace_id or "", r.rule_id or "",
                    r.method or "", r.path or "", r.status_code if r.status_code is not None else "",
                    r.duration_ms if r.duration_ms is not None else ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=system_logs.csv"},
    )


# ── Frontend log ingestion (any authenticated user) ──────────────

class ClientLogItem(BaseModel):
    level: str = "ERROR"
    event: str = Field("frontend.event", max_length=60)
    message: str = Field("", max_length=2000)
    path: Optional[str] = Field(None, max_length=255)
    details: Optional[dict] = None


class ClientLogBatch(BaseModel):
    events: list[ClientLogItem] = Field(default_factory=list)


@router.post("/client")
@limiter.limit("120/minute")
async def ingest_client_logs(
    request: Request,
    body: ClientLogBatch,
    current_user=Depends(get_current_user),
):
    """Frontend ships its own errors/warnings here. Identity is stamped from the
    token (never trusted from the body); level is clamped so a client cannot forge
    ALERT audit rows."""
    from app.services.system_logger import log_event

    ip = get_real_client_ip(request)
    tid = getattr(request.state, "trace_id", None)
    accepted = 0
    for item in body.events[:50]:  # cap batch
        lvl = item.level if item.level in ("INFO", "WARNING", "ERROR") else "ERROR"
        log_event(
            level=lvl, category="frontend", event=(item.event or "frontend.event"),
            message=(item.message or ""), source="frontend",
            username=getattr(current_user, "username", None),
            user_id=getattr(current_user, "id", None),
            source_ip=ip, trace_id=tid, path=item.path, details=item.details,
        )
        accepted += 1
    return APIResponse.ok(data={"accepted": accepted})
