"""Notification senders — discord, email, telegram, whatsapp."""
from __future__ import annotations

import logging
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path

import httpx
from aiosmtplib import SMTP

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_T = httpx.Timeout(10.0)
_T_LONG = httpx.Timeout(30.0)


async def _discord_message(message: str, config: dict | None = None) -> bool:
    url = (config or {}).get("webhook_url", settings.DISCORD_WEBHOOK_URL)
    if not url:
        logger.warning("Discord not configured — skipping")
        return False
    try:
        async with httpx.AsyncClient(timeout=_T) as c:
            r = await c.post(url, json={"content": message})
            r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Discord message failed: {e}")
        return False


async def _discord_file(file_path: str, message: str = "", config: dict | None = None) -> bool:
    url = (config or {}).get("webhook_url", settings.DISCORD_WEBHOOK_URL)
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=_T_LONG) as c:
            with open(file_path, "rb") as f:
                r = await c.post(url, data={"content": message} if message else {}, files={"file": (Path(file_path).name, f, "application/octet-stream")})
                r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Discord file failed: {e}")
        return False


async def _email_alert(subject: str, body: str, config: dict | None = None) -> bool:
    host = (config or {}).get("host", settings.SMTP_HOST)
    user = (config or {}).get("user", settings.SMTP_USER)
    pw = (config or {}).get("password", settings.SMTP_PASS)
    port = (config or {}).get("port", settings.SMTP_PORT) or 587
    sender = (config or {}).get("from_address", settings.SMTP_FROM_ADDRESS)
    if not all([host, user, pw]):
        logger.warning("SMTP not configured — skipping")
        return False
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = sender
    msg["Subject"] = f"[NOD Alert] {subject}"
    msg.attach(MIMEText(body, "plain"))
    try:
        async with SMTP(hostname=host, port=port, use_tls=True, timeout=10.0) as smtp:
            await smtp.login(user, pw)
            await smtp.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Email alert failed: {e}")
        return False


async def _email_with_attachment(subject: str, body: str, file_path: str, recipient: str | None = None) -> bool:
    if not all([settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASS]):
        return False
    to = recipient or settings.SMTP_FROM_ADDRESS
    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_FROM_ADDRESS
    msg["To"] = to
    msg["Subject"] = f"[NOD Report] {subject}"
    msg.attach(MIMEText(body, "plain"))
    with open(file_path, "rb") as f:
        a = MIMEBase("application", "octet-stream")
        a.set_payload(f.read())
        encoders.encode_base64(a)
        a.add_header("Content-Disposition", f'attachment; filename="{Path(file_path).name}"')
        msg.attach(a)
    try:
        async with SMTP(hostname=settings.SMTP_HOST, port=settings.SMTP_PORT, use_tls=True, timeout=30.0) as smtp:
            await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
            await smtp.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Email attachment failed: {e}")
        return False


async def _telegram_alert(message: str, config: dict | None = None) -> bool:
    token = (config or {}).get("bot_token", settings.TELEGRAM_BOT_TOKEN)
    chat = (config or {}).get("chat_id", settings.TELEGRAM_CHAT_ID)
    if not token or not chat:
        logger.warning("Telegram not configured — skipping")
        return False
    try:
        async with httpx.AsyncClient(timeout=_T) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": message, "parse_mode": "Markdown"})
            r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
        return False


async def _telegram_document(file_path: str, caption: str = "") -> bool:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return False
    try:
        async with httpx.AsyncClient(timeout=_T_LONG) as c:
            with open(file_path, "rb") as f:
                r = await c.post(f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendDocument", data={"chat_id": settings.TELEGRAM_CHAT_ID, "caption": caption}, files={"document": f})
                r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram document failed: {e}")
        return False


async def _whatsapp_message(message: str, config: dict | None = None) -> bool:
    token = (config or {}).get("api_token", settings.WHATSAPP_API_TOKEN)
    phone = (config or {}).get("phone_number_id", settings.WHATSAPP_PHONE_NUMBER_ID)
    if not token or not phone:
        logger.warning("WhatsApp not configured — skipping")
        return False
    try:
        async with httpx.AsyncClient(timeout=_T) as c:
            r = await c.post(
                f"https://graph.facebook.com/v19.0/{phone}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": message}},
            )
            r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"WhatsApp message failed: {e}")
        return False


async def _whatsapp_document(file_path: str, caption: str = "") -> bool:
    if not all([settings.WHATSAPP_API_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID]):
        return False
    try:
        async with httpx.AsyncClient(timeout=_T_LONG) as c:
            with open(file_path, "rb") as f:
                u = await c.post(
                    f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/media",
                    headers={"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"},
                    files={"file": f}, data={"messaging_product": "whatsapp"},
                )
                u.raise_for_status()
                media_id = u.json()["id"]
            r = await c.post(
                f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
                headers={"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"},
                json={"messaging_product": "whatsapp", "to": settings.WHATSAPP_PHONE_NUMBER_ID, "type": "document", "document": {"id": media_id, "caption": caption}},
            )
            r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"WhatsApp document failed: {e}")
        return False


# ponytail: dispatch tables — single surface for alert_engine + reports.
ALERT_DISPATCH = {
    "whatsapp": _whatsapp_message,
    "telegram": _telegram_alert,
    "smtp": _email_alert,
    "discord": _discord_message,
}

DOCUMENT_DISPATCH = {
    "email": _email_with_attachment,
    "telegram": _telegram_document,
    "discord": _discord_file,
    "whatsapp": _whatsapp_document,
}
