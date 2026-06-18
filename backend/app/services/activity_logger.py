"""
Activity logging utilities (FR-11, FR-15).
Writes user_activity_log records for auditable actions.
"""
from __future__ import annotations

from typing import Optional

from app.db.models import UserActivityLog
from app.db.session import AsyncSessionLocal


async def log_activity(
    user_id: str,
    action: str,
    source_ip: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """
    Fire-and-forget: write a user activity log entry.
    Does not raise — failures are silent to avoid breaking the main flow.
    """
    try:
        async with AsyncSessionLocal() as session:
            log_entry = UserActivityLog(
                user_id=user_id,
                action=action,
                source_ip=source_ip,
                details=details,
            )
            session.add(log_entry)
            await session.commit()
    except Exception:
        # Activity logging must never crash the request
        pass
