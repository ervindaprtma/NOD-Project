"""Notification channel configuration API (v3 §3.13).

Admin/Superadmin-gated CRUD for WhatsApp, Telegram, and SMTP channels.
Secrets are encrypted at rest via Fernet and masked on GET.
"""
from __future__ import annotations

import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
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


def _mask_config(config: dict, channel: str) -> dict:
    """Return a copy of config with only SECRET fields masked.

    Non-secret fields (chat_id, SMTP host/port/from_address, …) are returned as-is so
    the settings form can display and safely re-send them. Masking everything long
    caused those fields to round-trip back as their masked form and corrupt the config.
    """
    secret_fields = _SECRET_FIELDS_BY_CHANNEL.get(channel, set())
    return {
        k: (mask_secret(v) if (k in secret_fields and isinstance(v, str) and v) else v)
        for k, v in config.items()
    }


def _is_masked(value: str) -> bool:
    """True if a value looks like our masked form (mask_secret ends with '****').

    Guards against a form echoing a masked secret back on save — we keep the stored
    value instead of overwriting the real secret with '••••'.
    """
    return isinstance(value, str) and value.endswith("****")


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
                config=_mask_config(c.config, c.channel),
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
            config=_mask_config(cfg.config, cfg.channel),
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
            # Merge into the existing config so editing one field never wipes the others
            # (the form omits secrets it isn't changing). For secret fields, only overwrite
            # when a real new value is supplied — an empty or masked value keeps the stored
            # secret. Non-secret fields are written through as given.
            secret_fields = _SECRET_FIELDS_BY_CHANNEL.get(channel, set())
            merged = dict(cfg.config or {})
            for k, v in body.config.items():
                if k in secret_fields:
                    if isinstance(v, str) and v and not _is_masked(v):
                        merged[k] = encrypt_secret(v)
                    # else: keep the existing stored (encrypted) secret
                else:
                    merged[k] = v
            cfg.config = merged
        if body.recipients is not None:
            cfg.recipients = body.recipients
        if current_user:
            cfg.updated_by = current_user.id

        await db.commit()
        await db.refresh(cfg)

        # ALERT audit — record WHO changed the channel + enabled/severity, never the
        # secret (bot token / chat id are redacted by the logger regardless).
        try:
            from app.services.system_logger import log_event
            log_event(level="ALERT", category="alert", event="channel.updated",
                      message=f"Notification channel '{cfg.channel}' updated (enabled={cfg.enabled})",
                      source="frontend", username=getattr(current_user, "username", None),
                      user_id=getattr(current_user, "id", None),
                      details={"channel": cfg.channel, "enabled": cfg.enabled, "min_severity": cfg.min_severity})
        except Exception:
            pass

        data = NotificationConfigRead(
            channel=cfg.channel,
            enabled=cfg.enabled,
            min_severity=cfg.min_severity,
            config=_mask_config(cfg.config, cfg.channel),
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


class TelegramDiscoverRequest(BaseModel):
    # Optional freshly-typed token so discovery works before the config is saved;
    # falls back to the stored (decrypted) token when omitted.
    bot_token: Optional[str] = None


@router.post("/telegram/chats")
async def discover_telegram_chats(
    body: TelegramDiscoverRequest,
    current_user=Depends(require_role("admin")),
):
    """Find the chat_ids the Telegram bot can currently see (getUpdates helper).

    Uses the token from the request if provided (so it works before saving), else the
    stored one. Returns the distinct chats the bot has recently received a message in /
    been added to, so the admin can pick the right chat_id instead of guessing.
    """
    token = (body.bot_token or "").strip()
    if not token:
        async with AsyncSessionLocal() as db:
            cfg = await _get_config(db, "telegram")
        if cfg and cfg.config:
            decrypted = _decrypt_config(dict(cfg.config), "telegram")
            stored = decrypted.get("bot_token")
            if stored == "<decrypt-error>":
                raise HTTPException(
                    status_code=400,
                    detail="Cannot decrypt the saved bot token — re-enter it and save first.",
                )
            token = (stored or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Enter or save a Telegram bot token first.")

    from app.services.notifiers import telegram_discover_chats, NotifierError

    try:
        chats = await telegram_discover_chats(token)
    except NotifierError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return APIResponse.ok(data={"chats": chats})
