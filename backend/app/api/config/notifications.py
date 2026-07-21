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
from app.schemas.common import APIResponse, Meta
from app.schemas.notification import (
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
    "discord": {"webhook_url"},
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
    config: NotificationConfig | None = result.scalar_one_or_none()
    return config


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

    §11.5: When disabling a channel, includes warning in response body.
    """
    async with AsyncSessionLocal() as db:
        # §11.5: Check for enabled rules referencing this channel before disabling
        warning_rules = []
        if body.enabled is False:
            from app.db.models import AlertRule
            stmt = select(AlertRule.id, AlertRule.name).where(
                AlertRule.notify_channels.contains([channel])
                & (AlertRule.enabled == True)  # noqa: E712
            )
            result = await db.execute(stmt)
            warning_rules = [{"id": r[0], "name": r[1]} for r in result.all()]

        cfg = await _get_config(db, channel)
        is_new = cfg is None

        if is_new:
            cfg = NotificationConfig(channel=channel)
            db.add(cfg)

        # mypy: cfg is now guaranteed NotificationConfig, not Optional.
        assert cfg is not None

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

        data = NotificationConfigRead(
            channel=cfg.channel,
            enabled=cfg.enabled,
            min_severity=cfg.min_severity,
            config=_mask_config(cfg.config),
            recipients=cfg.recipients,
            updated_by=cfg.updated_by,
            updated_at=cfg.updated_at,
        )

        # §11.5: Include warning in response body if rules will lose notifications
        if warning_rules:
            return APIResponse.ok(
                data=data.model_dump(),
                message=f"Created" if is_new else f"Updated (warning: {len(warning_rules)} rule(s) will lose notifications)",
                meta=Meta(warning_rules=warning_rules),
            )

        return APIResponse.ok(
            data=data,
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

        # If the Fernet key rotated since the secret was saved, decryption yields this
        # sentinel; sending it would fail opaquely (Telegram 401, etc.). Say so plainly.
        bad_secrets = [k for k, v in decrypted.items() if v == "<decrypt-error>"]
        if bad_secrets:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot decrypt {', '.join(sorted(bad_secrets))} for '{channel}' — the "
                    f"encryption key changed since it was saved. Re-enter the {channel} "
                    f"credentials in Settings and save again."
                ),
            )

        from app.services.notifier_helper import send_alert
        from app.services.notifiers import NotifierError

        try:
            success = await send_alert(
                channel=channel,
                config=decrypted,
                subject="🧪 NOD Alert Test",
                body=f"🧪 Test notification from NOD Alert System\nChannel: {channel}\nSeverity: {cfg.min_severity}",
                raise_on_error=True,
            )
        except NotifierError as e:
            # 502: we reached the endpoint but the provider (or config) rejected the send.
            raise HTTPException(status_code=502, detail=f"{channel} test failed — {e}")

        if success:
            return APIResponse.ok(data={"sent": True, "channel": channel})
        raise HTTPException(status_code=502, detail=f"Failed to send test message via {channel}")
