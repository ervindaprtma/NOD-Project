"""
Email notification sender using aiosmtplib (async SMTP).
"""
from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from aiosmtplib import SMTP

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def send_email_alert(
    subject: str,
    body: str,
    recipient: str | None = None,
) -> bool:
    """
    Send an alert email via SMTP.
    Returns True on success.
    """
    if not all([settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASS]):
        logger.warning("SMTP not configured — skipping email alert")
        return False

    to_addr = recipient or settings.SMTP_FROM_ADDRESS

    message = MIMEMultipart("alternative")
    message["From"] = settings.SMTP_FROM_ADDRESS
    message["To"] = to_addr
    message["Subject"] = f"[NOD Alert] {subject}"
    message.attach(MIMEText(body, "plain"))

    try:
        async with SMTP(
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            use_tls=True,
            timeout=10.0,
        ) as smtp:
            await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
            await smtp.send_message(message)
            logger.info(f"Email alert sent to {to_addr}")
            return True
    except Exception as e:
        logger.error(f"Email alert failed: {e}")
        return False


async def send_email_with_attachment(
    subject: str,
    body: str,
    file_path: str,
    recipient: str | None = None,
) -> bool:
    """Send an email with a file attachment (for report distribution)."""
    if not all([settings.SMTP_HOST, settings.SMTP_USER, settings.SMTP_PASS]):
        return False

    to_addr = recipient or settings.SMTP_FROM_ADDRESS

    message = MIMEMultipart()
    message["From"] = settings.SMTP_FROM_ADDRESS
    message["To"] = to_addr
    message["Subject"] = f"[NOD Report] {subject}"
    message.attach(MIMEText(body, "plain"))

    # Attach file
    filename = Path(file_path).name
    with open(file_path, "rb") as f:
        attachment = MIMEBase("application", "octet-stream")
        attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        message.attach(attachment)

    try:
        async with SMTP(
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            use_tls=True,
            timeout=30.0,
        ) as smtp:
            await smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
            await smtp.send_message(message)
            logger.info(f"Email with attachment sent to {to_addr}")
            return True
    except Exception as e:
        logger.error(f"Email with attachment failed: {e}")
        return False
