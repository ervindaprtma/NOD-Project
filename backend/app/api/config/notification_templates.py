# PURPOSE: CRUD endpoints for notification_templates (§11.1)
# SECURITY NOTES: Templates rendered in Jinja2 sandbox; preview is admin-gated

from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func

from app.api.auth import require_role
from app.db.models import NotificationTemplate, AlertRule
from app.db.session import AsyncSessionLocal
from app.schemas.common import APIResponse
from app.schemas.notification_template import (
    NotificationTemplateCreate,
    NotificationTemplateUpdate,
    NotificationTemplateRead,
    NotificationTemplatePreview,
)
from app.services.alert_engine import _render_template

router = APIRouter(
    prefix="/api/v1/config/notification-templates",
    tags=["config", "notification-templates"],
    dependencies=[Depends(require_role("admin"))],
)


@router.get("")
async def list_notification_templates(current_user=Depends(require_role("admin"))):
    """GET /api/v1/config/notification-templates — list with used_by_count."""
    async with AsyncSessionLocal() as db:
        stmt = select(
            NotificationTemplate,
            func.count(AlertRule.id).label("used_by_count"),
        ).outerjoin(AlertRule).group_by(NotificationTemplate.id)

        result = await db.execute(stmt)
        rows = result.all()

        templates = []
        for tmpl, count in rows:
            data = NotificationTemplateRead.model_validate(tmpl)
            data.used_by_count = count or 0
            templates.append(data.model_dump())

        return APIResponse.ok(data=templates)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_notification_template(
    body: NotificationTemplateCreate,
    current_user=Depends(require_role("admin")),
):
    """POST /api/v1/config/notification-templates — create template."""
    async with AsyncSessionLocal() as db:
        tmpl = NotificationTemplate(
            name=body.name,
            description=body.description or "",
            subject_template=body.subject_template or "Alert: {{ rule.name }}",
            body_template=body.body_template,
            line_template=body.line_template,
            is_user_created=True,
        )
        db.add(tmpl)
        await db.commit()
        await db.refresh(tmpl)

        return APIResponse.ok(data={"id": tmpl.id})


@router.get("/{template_id}")
async def get_notification_template(
    template_id: str,
    current_user=Depends(require_role("admin")),
):
    """GET /api/v1/config/notification-templates/{id}."""
    async with AsyncSessionLocal() as db:
        tmpl = await db.get(NotificationTemplate, template_id)
        if not tmpl:
            raise HTTPException(status_code=404, detail="Template not found")

        count = await db.scalar(
            select(func.count(AlertRule.id)).where(
                AlertRule.notification_template_id == template_id
            )
        )

        data = NotificationTemplateRead.model_validate(tmpl)
        data.used_by_count = count or 0

        return APIResponse.ok(data=data.model_dump())


@router.put("/{template_id}")
async def update_notification_template(
    template_id: str,
    body: NotificationTemplateUpdate,
    current_user=Depends(require_role("admin")),
):
    """PUT /api/v1/config/notification-templates/{id}."""
    async with AsyncSessionLocal() as db:
        tmpl = await db.get(NotificationTemplate, template_id)
        if not tmpl:
            raise HTTPException(status_code=404, detail="Template not found")

        if body.name is not None:
            tmpl.name = body.name
        if body.description is not None:
            tmpl.description = body.description
        if body.subject_template is not None:
            tmpl.subject_template = body.subject_template
        if body.body_template is not None:
            tmpl.body_template = body.body_template
        if body.line_template is not None:
            tmpl.line_template = body.line_template

        await db.commit()
        return APIResponse.ok(data={})


@router.delete("/{template_id}")
async def delete_notification_template(
    template_id: str,
    current_user=Depends(require_role("admin")),
):
    """DELETE /api/v1/config/notification-templates/{id} — 409 if referenced."""
    async with AsyncSessionLocal() as db:
        # Fetch referencing rules with their names (for §11.5 UX safeguard)
        stmt = select(AlertRule.id, AlertRule.name).where(
            AlertRule.notification_template_id == template_id
        )
        result = await db.execute(stmt)
        rule_rows = result.all()  # list of Row objects

        if rule_rows:
            rule_list = [{"id": r[0], "name": r[1]} for r in rule_rows]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": f"Template referenced by {len(rule_rows)} rule(s)",
                    "referencing_rules": rule_list,
                },
            )

        tmpl = await db.get(NotificationTemplate, template_id)
        if not tmpl:
            raise HTTPException(status_code=404, detail="Template not found")

        await db.delete(tmpl)
        await db.commit()
        return APIResponse.ok(data={})


@router.post("/{template_id}/preview")
async def preview_notification_template(
    template_id: str,
    body: NotificationTemplatePreview,
    current_user=Depends(require_role("admin")),
):
    """POST /api/v1/config/notification-templates/{id}/preview — sandboxed dry-run."""
    async with AsyncSessionLocal() as db:
        tmpl = await db.get(NotificationTemplate, template_id)
        if not tmpl:
            raise HTTPException(status_code=404, detail="Template not found")

        ctx = {
            "rule": {
                "name": body.name or "Test Rule",
                "severity": body.severity or "WARNING",
                "site_name": body.site_name,
                "metric_field": body.metric_field or "cpu.usage",
                "condition": body.condition or ">",
                "threshold_value": body.threshold_value or 80.0,
            },
            "metric_value": body.metric_value or 95.5,
        }

        rendered = {}
        try:
            rendered["subject"] = _render_template(tmpl.subject_template, ctx)
        except Exception as e:
            raise HTTPException(
                status_code=422, detail=f"Subject render failed: {e}"
            )

        try:
            rendered["body"] = _render_template(tmpl.body_template, ctx)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Body render failed: {e}")

        # Only render line_template if it exists
        if tmpl.line_template:
            try:
                rendered["line"] = _render_template(tmpl.line_template, ctx)
            except Exception as e:
                raise HTTPException(status_code=422, detail=f"Line render failed: {e}")
        else:
            rendered["line"] = ""

        return APIResponse.ok(data=rendered)