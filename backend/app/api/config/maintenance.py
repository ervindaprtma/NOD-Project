"""Maintenance window CRUD (v3 §3.14).

Planned maintenance windows suppress alert evaluation for specific sites
during the window. Only admin/superadmin can create/delete.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_role
from app.db.models import MaintenanceWindow
from app.db.session import get_db
from app.schemas.common import APIResponse

router = APIRouter(
    prefix="/api/v1/config/maintenance",
    tags=["config", "maintenance"],
    dependencies=[Depends(require_role("admin"))],
)


# ── Schemas ──


class MaintenanceWindowCreate(BaseModel):
    site_name: str = Field(..., max_length=128)
    starts_at: datetime
    ends_at: datetime
    reason: str = ""


class MaintenanceWindowRead(BaseModel):
    id: str
    site_name: str
    starts_at: datetime
    ends_at: datetime
    reason: str
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Endpoints ──


@router.get("", response_model=APIResponse[list[MaintenanceWindowRead]])
async def list_maintenance_windows(
    include_past: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List maintenance windows. Default: only active + future."""
    stmt = select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at)
    if not include_past:
        now = datetime.now(timezone.utc)
        stmt = stmt.where(MaintenanceWindow.ends_at >= now)
    result = await db.execute(stmt)
    return APIResponse.ok(data=result.scalars().all())


@router.post("", response_model=APIResponse[MaintenanceWindowRead], status_code=201)
async def create_maintenance_window(
    body: MaintenanceWindowCreate,
    current_user=Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new maintenance window."""
    if body.ends_at <= body.starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")

    mw = MaintenanceWindow(
        site_name=body.site_name,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        reason=body.reason,
        created_by=current_user.id,
    )
    db.add(mw)
    await db.commit()
    await db.refresh(mw)
    return APIResponse.ok(data=mw)


@router.delete("/{mw_id}", response_model=APIResponse[dict])
async def delete_maintenance_window(
    mw_id: str,
    current_user=Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Delete a maintenance window."""
    mw = await db.get(MaintenanceWindow, mw_id)
    if not mw:
        raise HTTPException(status_code=404, detail="Maintenance window not found")
    await db.delete(mw)
    await db.commit()
    return APIResponse.ok(data={"deleted": mw_id})
