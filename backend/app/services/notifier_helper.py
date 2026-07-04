"""Unified notification dispatch helper (v3 §3.13).

alert_engine calls send_alert() which loads the enabled channel configs from DB,
decrypts secrets, and calls the appropriate notifier module.

Notifier modules (telegram.py, email.py, whatsapp.py) now accept a config dict
as first argument so they work with both env-var and DB config.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import NotificationConfig
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def load_channel_configs(
    min_severity: str | None = None,
) -> dict[str, dict]:
    """Load enabled notification configs from DB, decrypting secrets.

    Returns dict of {channel_name: decrypted_config_dict}.
    Only channels with enabled=True and config data are returned.
    """
    from app.core.security import decrypt_secret

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(NotificationConfig).where(NotificationConfig.enabled == True)
        )
        rows = result.scalars().all()

    channels: dict[str, dict] = {}
    for row in rows:
        if row.min_severity and min_severity:
            severity_order = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
            if severity_order.get(min_severity, 0) < severity_order.get(row.min_severity, 2):
                # Rule severity is below this channel's minimum
                continue

        decrypted = dict(row.config)
        # Decrypt known secret fields
        secret_fields = {
            "whatsapp": {"api_token", "phone_number_id"},
            "telegram": {"bot_token"},
            "smtp": {"user", "password"},
        }.get(row.channel, set())

        for key in secret_fields:
            if key in decrypted and decrypted[key]:
                try:
                    decrypted[key] = decrypt_secret(decrypted[key])
                except Exception:
                    logger.warning(f"Failed to decrypt {key} for channel {row.channel}")
                    decrypted[key] = ""

        channels[row.channel] = decrypted

    return channels


async def send_alert(
    channel: str,
    config: dict,
    subject: str,
    body: str,
    severity: str = "CRITICAL",
) -> bool:
    """Dispatch an alert notification via the specified channel.

    Args:
        channel: whatsapp, telegram, smtp
        config: Decrypted config dict for the channel
        subject: Short alert subject/title
        body: Full alert message body
        severity: INFO | WARNING | CRITICAL

    Returns:
        True if the notification was sent, False otherwise.
    """
    if channel == "whatsapp":
        from app.services.notifiers.whatsapp import send_whatsapp_message
        return await send_whatsapp_message(message=body, config=config)
    elif channel == "telegram":
        from app.services.notifiers.telegram import send_telegram_alert
        return await send_telegram_alert(message=body, config=config)
    elif channel == "smtp":
        from app.services.notifiers.email import send_email_alert
        return await send_email_alert(subject=subject, body=body, config=config)
    else:
        logger.warning(f"Unknown notification channel: {channel}")
        return False


async def send_test_notification(
    channel: str,
    config: dict,
    message: str,
) -> bool:
    """Send a test message. Used by the test-endpoint and during setup."""
    return await send_alert(
        channel=channel,
        config=config,
        subject="NOD Alert - Test",
        body=message,
        severity="INFO",
    )
