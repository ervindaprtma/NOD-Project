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


async def send_telegram_alert(message: str) -> bool:
    """
    Send a text alert via Telegram Bot API.
    Returns True on success, False on failure.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping alert dispatch")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
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
        async with httpx.AsyncClient(timeout=30.0) as client:
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
