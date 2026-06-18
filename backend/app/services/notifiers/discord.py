"""
Discord notification sender via Webhook.
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def send_discord_message(message: str) -> bool:
    """
    Send a text message to Discord via Webhook.
    """
    if not settings.DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook not configured — skipping")
        return False

    payload = {"content": message}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.DISCORD_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
            logger.info("Discord message sent")
            return True
    except Exception as e:
        logger.error(f"Discord message failed: {e}")
        return False


async def send_discord_file(file_path: str, message: str = "") -> bool:
    """
    Send a file attachment to Discord via Webhook using multipart/form-data.
    """
    if not settings.DISCORD_WEBHOOK_URL:
        return False

    filename = Path(file_path).name

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            with open(file_path, "rb") as f:
                files = {
                    "file": (filename, f, "application/octet-stream"),
                }
                data = {"content": message} if message else {}
                resp = await client.post(
                    settings.DISCORD_WEBHOOK_URL,
                    data=data,
                    files=files,
                )
                resp.raise_for_status()
            logger.info(f"Discord file sent: {filename}")
            return True
    except Exception as e:
        logger.error(f"Discord file send failed: {e}")
        return False
