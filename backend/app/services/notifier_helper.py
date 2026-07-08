"""Unified notification dispatch helper (v3 §3.13).

alert_engine calls send_alert() which dispatches via the notifier module's
ALERT_DISPATCH table — single surface, no per-channel if/else.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import NotificationConfig
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def load_channel_configs(min_severity: str | None = None) -> dict[str, dict]:
    """Load enabled notification configs from DB, decrypting secrets."""
    from app.api.config.notifications import _SECRET_FIELDS_BY_CHANNEL
    from app.core.security import decrypt_secret

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(NotificationConfig).where(NotificationConfig.enabled == True)
        )).scalars().all()

    severity_order = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}

    channels: dict[str, dict] = {}
    for row in rows:
        if row.min_severity and min_severity and \
                severity_order.get(min_severity, 0) < severity_order.get(row.min_severity, 2):
            continue
        decrypted = dict(row.config)
        for key in _SECRET_FIELDS_BY_CHANNEL.get(row.channel, set()):
            if decrypted.get(key):
                try:
                    decrypted[key] = decrypt_secret(decrypted[key])
                except Exception:
                    logger.warning(f"Failed to decrypt {key} for channel {row.channel}")
                    decrypted[key] = ""
        channels[row.channel] = decrypted
    return channels


async def send_alert(channel: str, config: dict, subject: str, body: str, severity: str = "CRITICAL") -> bool:
    """Dispatch an alert via the named channel. Returns True on success."""
    from app.services.notifiers import ALERT_DISPATCH

    fn = ALERT_DISPATCH.get(channel)
    if not fn:
        logger.warning(f"Unknown notification channel: {channel}")
        return False
    # ponytail: smtp is the only channel needing a subject.
    if channel == "smtp":
        return await fn(subject=subject, body=body, config=config)
    return await fn(message=body, config=config)


async def send_test_notification(channel: str, config: dict, message: str) -> bool:
    """Send a test notification through the same dispatch path as production
    alerts so the two never drift. Routes via send_alert() rather than calling
    ALERT_DISPATCH directly.
    """
    return await send_alert(
        channel=channel,
        config=config,
        subject="🧪 NOD Alert Test",
        body=message,
    )
