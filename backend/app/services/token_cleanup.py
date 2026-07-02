"""
Token cleanup job — deletes expired/revoked refresh tokens older than 24h.
Runs hourly via the existing APScheduler instance.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.models import RefreshToken
from app.db.session import AsyncSessionLocal

logger = logging.getLogger("nod.services.token_cleanup")

# Delete tokens that are revoked OR expired, and older than 24h past expiry.
# Keeps recent revoked tokens for audit (e.g. Active Sessions table history).
RETENTION_HOURS = 24


async def cleanup_expired_tokens():
    """Delete refresh tokens that are revoked or expired and older than RETENTION_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                delete(RefreshToken).where(
                    RefreshToken.expires_at < cutoff
                )
            )
            await session.commit()
            count = getattr(result, "rowcount", 0) or 0
            if count:
                logger.info(f"Cleaned up {count} expired refresh tokens (older than {RETENTION_HOURS}h)")
    except Exception as e:
        logger.error(f"Token cleanup failed: {e}")


def start_token_cleanup_scheduler():
    """Start hourly token cleanup on the shared alert scheduler."""
    from app.services.alert_engine import scheduler
    scheduler.add_job(
        cleanup_expired_tokens,
        "interval",
        hours=1,
        id="token_cleanup",
        replace_existing=True,
        max_instances=1,
    )
    logger.info("Token cleanup scheduler started (interval=1h)")
