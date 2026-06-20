"""
WhatsApp notification sender via WhatsApp Business Cloud API.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

WHATSAPP_API_BASE = "https://graph.facebook.com/v19.0"


async def send_whatsapp_message(
    message: str,
    recipient_phone: str | None = None,
) -> bool:
    """
    Send a WhatsApp text message via Business Cloud API.
    """
    if not all([settings.WHATSAPP_API_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID]):
        logger.warning("WhatsApp not configured — skipping")
        return False

    to_number = recipient_phone or settings.WHATSAPP_PHONE_NUMBER_ID

    url = f"{WHATSAPP_API_BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message},
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            logger.info(f"WhatsApp message sent to {to_number}")
            return True
    except Exception as e:
        logger.error(f"WhatsApp message failed: {e}")
        return False


async def send_whatsapp_document(
    file_path: str,
    caption: str = "",
    recipient_phone: str | None = None,
) -> bool:
    """
    Send a document via WhatsApp using media upload + send flow.
    """
    if not all([settings.WHATSAPP_API_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID]):
        return False

    to_number = recipient_phone or settings.WHATSAPP_PHONE_NUMBER_ID
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            # Step 1: Upload media
            upload_url = f"{WHATSAPP_API_BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/media"
            with open(file_path, "rb") as f:
                files = {"file": f}
                data = {"messaging_product": "whatsapp"}
                upload_resp = await client.post(
                    upload_url, headers=headers, files=files, data=data
                )
                upload_resp.raise_for_status()
                media_id = upload_resp.json()["id"]

            # Step 2: Send document message
            msg_url = f"{WHATSAPP_API_BASE}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": to_number,
                "type": "document",
                "document": {
                    "id": media_id,
                    "caption": caption,
                },
            }
            msg_resp = await client.post(msg_url, json=payload, headers=headers)
            msg_resp.raise_for_status()

            logger.info(f"WhatsApp document sent to {to_number}")
            return True
    except Exception as e:
        logger.error(f"WhatsApp document send failed: {e}")
        return False
