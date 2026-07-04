"""Notification channel configuration API (v3 §3.13).

Admin/Superadmin-gated CRUD for WhatsApp, Telegram, and SMTP channels.
Secrets are encrypted at rest via Fernet and masked on GET.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_role
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)
from app.db.models import NotificationConfig
from app.db.session import AsyncSessionLocal
from app.schemas.common import APIResponse
from app.schemas.notification import (
    NotificationConfigCreate,
    NotificationConfigRead,
    NotificationConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/config/notifications",
    tags=["Config"],
)


# ── Helpers ───────────────────────────────────────────────────


_SECRET_FIELDS_BY_CHANNEL = {
    "whatsapp": {"api_token", "phone_number_id"},
    "telegram": {"bot_token"},
    "smtp": {"user", "password"},
}


def _mask_config(config: dict) -> dict:
    """Return a copy of config with secret values masked."""
    return {k: mask_secret(v) if isinstance(v, str) and len(v) > 4 else v for k, v in config.items()}


def _encrypt_config(config: dict, channel: str) -> dict:
    """Encrypt known secret fields in-place."""
    secret_fields = _SECRET_FIELDS_BY_CHANNEL.get(channel, set())
    out = dict(config)
    for key in secret_fields:
        if key in out and out[key]:
            out[key] = encrypt_secret(out[key])
    return out


def _decrypt_config(config: dict, channel: str) -> dict:
    """Decrypt known secret fields in-place."""
    secret_fields = _SECRET_FIELDS_BY_CHANNEL.get(channel, set())
    out = dict(config)
    for key in secret_fields:
        if key in out and out[key]:
            try:
                out[key] = decrypt_secret(out[key])
            except Exception:
                out[key] = "<decrypt-error>"
    return out


async def _get_config(db: AsyncSession, channel: str) -> NotificationConfig | None:
    result = await db.execute(
        select(NotificationConfig).where(NotificationConfig.channel == channel)
    )
    return result.scalar_one_or_none()


# ── Endpoints ─────────────────────────────────────────────────


@router.get("")
async def list_configs(
    current_user=Depends(require_role("admin")),
):
    """List all configured notification channels with masked secrets."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(NotificationConfig).order_by(NotificationConfig.channel))
        configs = result.scalars().all()
        return APIResponse.ok(data={
            c.channel: NotificationConfigRead(
                channel=c.channel,
                enabled=c.enabled,
                min_severity=c.min_severity,
                config=_mask_config(c.config),
                recipients=c.recipients,
                updated_by=c.updated_by,
                updated_at=c.updated_at,
            ) for c in configs
        })


@router.get("/{channel}")
async def get_config(
    channel: str,
    current_user=Depends(require_role("admin")),
):
    """Get one channel config with secrets masked."""
    async with AsyncSessionLocal() as db:
        cfg = await _get_config(db, channel)
        if not cfg:
            raise HTTPException(status_code=404, detail=f"No config for channel '{channel}'")
        return APIResponse.ok(data=NotificationConfigRead(
            channel=cfg.channel,
            enabled=cfg.enabled,
            min_severity=cfg.min_severity,
            config=_mask_config(cfg.config),
            recipients=cfg.recipients,
            updated_by=cfg.updated_by,
            updated_at=cfg.updated_at,
        ))


@router.put("/{channel}")
async def upsert_config(
    channel: str,
    body: NotificationConfigUpdate,
    current_user=Depends(require_role("admin")),
):
    """Create or update a notification channel config.

    Secrets (api_token, bot_token, password, etc.) are encrypted at rest.
    Only provided fields are updated — missing fields keep their existing values.
    """
    async with AsyncSessionLocal() as db:
        cfg = await _get_config(db, channel)
        is_new = cfg is None

        if is_new:
            cfg = NotificationConfig(channel=channel)
            db.add(cfg)

        if body.enabled is not None:
            cfg.enabled = body.enabled
        if body.min_severity is not None:
            cfg.min_severity = body.min_severity
        if body.config is not None:
            cfg.config = _encrypt_config(body.config, channel)
        if body.recipients is not None:
            cfg.recipients = body.recipients
        if current_user:
            cfg.updated_by = current_user.id

        await db.commit()
        await db.refresh(cfg)

        return APIResponse.ok(
            data=NotificationConfigRead(
                channel=cfg.channel,
                enabled=cfg.enabled,
                min_severity=cfg.min_severity,
                config=_mask_config(cfg.config),
                recipients=cfg.recipients,
                updated_by=cfg.updated_by,
                updated_at=cfg.updated_at,
            ),
            message="Created" if is_new else "Updated",
        )


@router.delete("/{channel}")
async def delete_config(
    channel: str,
    current_user=Depends(require_role("superadmin")),
):
    """Delete a notification channel config entirely (Superadmin only)."""
    async with AsyncSessionLocal() as db:
        cfg = await _get_config(db, channel)
        if not cfg:
            raise HTTPException(status_code=404, detail=f"No config for channel '{channel}'")
        await db.delete(cfg)
        await db.commit()
        return APIResponse.ok(data={"deleted": channel})


@router.post("/{channel}/test")
async def test_config(
    channel: str,
    current_user=Depends(require_role("admin")),
):
    """Send a test notification via the configured channel."""
    async with AsyncSessionLocal() as db:
        cfg = await _get_config(db, channel)
        if not cfg or not cfg.enabled:
            raise HTTPException(status_code=400, detail=f"Channel '{channel}' is not configured or not enabled")

        if not cfg.config:
            raise HTTPException(status_code=400, detail=f"Channel '{channel}' has no config data")

        decrypted = _decrypt_config(dict(cfg.config), channel)

        from app.services.notifier_helper import send_test_notification

        success = await send_test_notification(
            channel=channel,
            config=decrypted,
            message=f"🧪 Test notification from NOD Alert System\nChannel: {channel}\nSeverity: {cfg.min_severity}",
        )

        if success:
            return APIResponse.ok(data={"sent": True, "channel": channel})
        else:
            raise HTTPException(status_code=500, detail=f"Failed to send test message via {channel}")
