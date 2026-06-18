"""
User Activity Logs API (FR-11).
Superadmin-only: enforced via require_role dependency, not just frontend routing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.auth import require_role
from app.db.models import User, UserActivityLog
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
    """
    FR-11: User activity audit log.
    ONLY superadmin can access this endpoint.
    Returns username and role alongside each log entry via JOIN.
    """
    # Build query with join to get username + role
    query = (
        select(UserActivityLog)
        .order_by(UserActivityLog.timestamp.desc())
    )

    if user_id:
        query = query.where(UserActivityLog.user_id == user_id)

    result = await db.execute(query.offset(offset).limit(limit))
    logs = result.scalars().all()

    # Batch-fetch all referenced users (single query, no N+1)
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
