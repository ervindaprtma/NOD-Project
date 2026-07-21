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


class NotifierError(Exception):
    """A notifier failed to send; carries a human-readable reason for the UI + logs."""

# ponytail: §9.2 SSRF egress allow-list. User-supplied webhook URLs are POSTed
# to — an unvalidated URL is an SSRF vector (OWASP A10). Allow only Discord's
# webhook hostname. If a future deployment needs a custom host, add it here
# with a one-line reason.
_ALLOWED_DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com", "canary.discord.com"})


def _check_discord_url(url: str) -> str | None:
    """Return the URL if its host is on the allow-list, else None. Strips
    userinfo to prevent e.g. https://discord.com@attacker.example/."""
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    if p.hostname is None:
        return None
    if p.hostname.lower() not in _ALLOWED_DISCORD_HOSTS:
        return None
    return url


async def _discord_message(message: str, config: dict | None = None) -> bool:
    raw = (config or {}).get("webhook_url", settings.DISCORD_WEBHOOK_URL)
    url = _check_discord_url(raw) if raw else None
    if not url:
        logger.warning("Discord webhook host not in allow-list — skipping")
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
    raw = (config or {}).get("webhook_url", settings.DISCORD_WEBHOOK_URL)
    url = _check_discord_url(raw) if raw else None
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
        raise NotifierError("Telegram bot_token or chat_id is missing.")
    try:
        async with httpx.AsyncClient(timeout=_T) as c:
            # Plain text — no parse_mode. Metric fields contain '_' (e.g.
            # active_sslvpn_users_count), which Telegram's Markdown parser rejects with
            # a 400 "can't parse entities", silently dropping real alerts.
            r = await c.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": message},
            )
    except httpx.RequestError as e:
        # DNS/connect/timeout — the server can't reach Telegram (egress/firewall).
        raise NotifierError(f"Could not reach Telegram: {e}") from e
    if r.status_code != 200:
        # Telegram replies {"ok": false, "description": "..."} — surface the real reason
        # (bad token → 401 Unauthorized, wrong chat → 400 "chat not found", etc.).
        try:
            desc = r.json().get("description") or r.text
        except Exception:
            desc = r.text
        raise NotifierError(f"Telegram API {r.status_code}: {desc}")
    return True


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
from typing import Awaitable, Callable

AlertSender = Callable[..., Awaitable[bool]]
ALERT_DISPATCH: dict[str, AlertSender] = {
    "whatsapp": _whatsapp_message,
    "telegram": _telegram_alert,
    "smtp": _email_alert,
    "discord": _discord_message,
}

DocumentSender = Callable[..., Awaitable[bool]]
DOCUMENT_DISPATCH: dict[str, DocumentSender] = {
    "email": _email_with_attachment,
    "telegram": _telegram_document,
    "discord": _discord_file,
    "whatsapp": _whatsapp_document,
}
