"""
Notifications API (FR-10).
Fetch user notifications, mark as read.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.models import Notification
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.notification import NotificationRead

router = APIRouter(prefix="/api/v1/notifications", tags=["Notifications"])


@router.get("", response_model=APIResponse[list[NotificationRead]])
async def get_notifications(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    """Fetch current user's notifications."""
    query = select(Notification).where(Notification.user_id == current_user.id)

    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712

    result = await db.execute(
        query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
    )
    notifications = result.scalars().all()

    return APIResponse.ok(
        data=[NotificationRead.model_validate(n) for n in notifications],
    )


@router.patch("/{notification_id}/read", response_model=APIResponse[dict])
async def mark_notification_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found.")

    notif.is_read = True
    await db.flush()
    return APIResponse.ok(data={"marked_read": notification_id})


@router.post("/mark-all-read", response_model=APIResponse[dict])
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Mark all notifications as read for current user."""
    result = await db.execute(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        )
    )
    notifications = result.scalars().all()
    for n in notifications:
        n.is_read = True
    await db.flush()
    return APIResponse.ok(data={"marked_read_count": len(notifications)})
