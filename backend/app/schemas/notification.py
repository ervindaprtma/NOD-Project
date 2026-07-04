"""Notification channel configuration schemas (v3 §3.13).

WhatsApp, Telegram, and SMTP credential management.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Individual channel config shapes ──────────────────────────


class WhatsAppConfigPayload(BaseModel):
    api_token: str = Field("", description="WhatsApp Cloud API token (encrypted at rest)")
    phone_number_id: str = Field("", description="Phone number ID from Meta Business")
    business_account_id: str = Field("", description="WhatsApp Business Account ID")


class TelegramConfigPayload(BaseModel):
    bot_token: str = Field("", description="Telegram Bot API token (encrypted at rest)")
    chat_id: str = Field("", description="Target chat/group ID")


class SMTPConfigPayload(BaseModel):
    host: str = Field("", description="SMTP server hostname")
    port: int = Field(587, description="SMTP server port")
    user: str = Field("", description="SMTP username (encrypted at rest)")
    password: str = Field("", description="SMTP password (encrypted at rest)")
    from_address: str = Field("", description="Sender email address")


# ── CRUD schemas ──────────────────────────────────────────────


class NotificationConfigCreate(BaseModel):
    """Create or update a notification channel config.

    All secrets sent as plaintext; the API encrypts them before storage.
    """
    channel: str = Field(..., pattern=r"^(whatsapp|telegram|smtp)$")
    enabled: bool = True
    min_severity: str = Field("CRITICAL", pattern=r"^(INFO|WARNING|CRITICAL)$")
    config: dict[str, Any] = Field(default_factory=dict)
    recipients: dict[str, Any] | None = None


class NotificationConfigUpdate(BaseModel):
    """Partial update — only provided fields are changed."""
    enabled: bool | None = None
    min_severity: str | None = Field(None, pattern=r"^(INFO|WARNING|CRITICAL)$")
    config: dict[str, Any] | None = None
    recipients: dict[str, Any] | None = None


class NotificationConfigRead(BaseModel):
    """Response shape with secrets masked."""
    channel: str
    enabled: bool
    min_severity: str
    config: dict[str, Any]  # secrets masked server-side before serialization
    recipients: dict[str, Any] | None
    updated_by: str | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class NotificationConfigSummary(BaseModel):
    """Lightweight summary for listing all channels."""
    channels: dict[str, NotificationConfigRead] = Field(
        default_factory=dict,
        description="One entry per configured channel"
    )
