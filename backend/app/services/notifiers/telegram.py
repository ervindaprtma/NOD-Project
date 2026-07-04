"""
Telegram notification sender using Bot API via httpx.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


async def send_telegram_alert(message: str, config: dict | None = None) -> bool:
    """
    Send a text alert via Telegram Bot API.
    config dict overrides settings.* when provided (DB config path).
    Returns True on success, False on failure.
    """
    bot_token = (config or {}).get("bot_token", settings.TELEGRAM_BOT_TOKEN)
    chat_id = (config or {}).get("chat_id", settings.TELEGRAM_CHAT_ID)

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured — skipping alert dispatch")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info(f"Telegram alert sent: {resp.status_code}")
            return True
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
        return False


async def send_telegram_document(file_path: str, caption: str = "") -> bool:
    """
    Send a document (report) via Telegram Bot API.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return False

    url = f"{TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendDocument"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {"chat_id": settings.TELEGRAM_CHAT_ID, "caption": caption}
                resp = await client.post(url, data=data, files=files)
                resp.raise_for_status()
            logger.info(f"Telegram document sent: {file_path}")
            return True
    except Exception as e:
        logger.error(f"Telegram document send failed: {e}")
        return False
