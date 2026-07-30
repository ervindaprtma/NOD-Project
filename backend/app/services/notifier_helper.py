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
        # §9.7: fail-closed on unknown severity values. A typo'd
        # min_severity="HIIGH" used to silently mean "always send" because
        # .get() defaulted to 0; we now drop the channel with a loud log
        # so the misconfig shows up in dashboards, not in a flood of
        # over-permissive notifications.
        if min_severity and min_severity not in severity_order:
            logger.error(
                "Unknown rule severity %r — skipping ALL channels, not defaulting",
                min_severity,
            )
            return {}
        if row.min_severity not in severity_order:
            logger.error(
                "Unknown channel min_severity %r on channel=%s — skipping channel",
                row.min_severity, row.channel,
            )
            continue
        if row.min_severity and min_severity and \
                severity_order[min_severity] < severity_order[row.min_severity]:
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


async def send_alert(
    channel: str, config: dict, subject: str, body: str,
    severity: str = "CRITICAL", raise_on_error: bool = False,
    parse_mode: str | None = None,
) -> bool:
    """Dispatch an alert via the named channel. Returns True on success.

    The fire path (alert_engine) wants a quiet bool — a failed channel must not abort
    the batch — so failures are logged and return False. The test endpoint passes
    raise_on_error=True to get the real reason (NotifierError) surfaced to the admin
    instead of an opaque failure.

    parse_mode (e.g. "HTML") only applies to Telegram — the HTML-styled templates. Other
    channels ignore it (Discord/email don't take it), so those would show raw tags; the
    HTML templates are Telegram-first.
    """
    from app.services.notifiers import ALERT_DISPATCH, NotifierError

    fn = ALERT_DISPATCH.get(channel)
    if not fn:
        if raise_on_error:
            raise NotifierError(f"Unknown notification channel: {channel}")
        logger.warning(f"Unknown notification channel: {channel}")
        return False
    try:
        # ponytail: smtp is the only channel needing a subject.
        if channel == "smtp":
            ok = await fn(subject=subject, body=body, config=config)
        elif channel == "telegram":
            ok = await fn(message=body, config=config, parse_mode=parse_mode)
        else:
            ok = await fn(message=body, config=config)
    except NotifierError as e:
        if raise_on_error:
            raise
        logger.error(f"{channel} send failed: {e}")
        return False
    if not ok and raise_on_error:
        raise NotifierError(f"{channel} rejected the message (see server logs for details).")
    return ok
