"""
User Management API (FR-06).
RBAC enforcement: admin+ can manage users; viewer/operator get 403.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, require_role
from app.core.security import hash_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.user import (
    ChangePasswordRequest,
    UserCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)
from app.services.activity_logger import log_activity

router = APIRouter(prefix="/api/v1/users", tags=["Users"])


# ═══════════════════════════════════════════════════════════════
# Self-service routes (must be before /{user_id} dynamic route)
# ═══════════════════════════════════════════════════════════════


@router.get("/me", response_model=APIResponse[UserRead])
async def get_own_profile(
    current_user=Depends(get_current_user),
):
    """Get own profile info (for header display, settings)."""
    return APIResponse.ok(data=UserRead.model_validate(current_user))


@router.put("/me", response_model=APIResponse[UserRead])
async def update_own_profile(
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """FR-07: Update own display name. Role and is_active are ignored for self-update."""
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key in ("full_name", "email"):
            setattr(current_user, key, value)
    await db.flush()
    await db.refresh(current_user)
    return APIResponse.ok(data=UserRead.model_validate(current_user))


@router.put("/me/password", response_model=APIResponse[dict])
async def change_own_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """FR-07: Change own password."""
    from app.core.security import verify_password

    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    current_user.hashed_password = hash_password(body.new_password)
    current_user.must_change_password = False
    await db.flush()
    return APIResponse.ok(data={"message": "Password changed successfully."})


# ═══════════════════════════════════════════════════════════════
# Admin-only CRUD routes
# ═══════════════════════════════════════════════════════════════


@router.get("", response_model=APIResponse[UserListResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
    limit: int = 50,
    offset: int = 0,
):
    """List all users (admin+ only)."""
    result = await db.execute(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    )
    users = result.scalars().all()
    total = (await db.execute(select(func.count(User.id)))).scalar() or 0
    return APIResponse.ok(
        data=UserListResponse(
            users=[UserRead.model_validate(u) for u in users],
            total=total,
        ),
        meta={"total": total},
    )


@router.post("", response_model=APIResponse[UserRead], status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """Create a new user."""
    result = await db.execute(select(User).where(User.username == body.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already exists.")

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists.")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    await log_activity(
        user_id=current_user.id,
        action="user_created",
        source_ip=db.info.get("source_ip"),
        details={"created_username": user.username, "created_role": user.role},
    )

    return APIResponse.ok(data=UserRead.model_validate(user))


@router.put("/{user_id}", response_model=APIResponse[UserRead])
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """Update user (admin+). Superadmin cannot have role changed by admin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Cannot modify superadmin account.")

    update_data = body.model_dump(exclude_unset=True)
    changes = {}
    for key, value in update_data.items():
        if key == "role" and user.role == "superadmin":
            continue
        old_val = getattr(user, key, None)
        setattr(user, key, value)
        if old_val != value:
            changes[key] = {"old": old_val, "new": value}
    await db.flush()
    await db.refresh(user)

    if changes:
        await log_activity(
            user_id=current_user.id,
            action="user_updated",
            details={"target_username": user.username, "changes": changes},
        )

    return APIResponse.ok(data=UserRead.model_validate(user))


@router.delete("/{user_id}", response_model=APIResponse[dict])
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """Hard-delete user (admin+). Superadmin cannot be deleted. Self-delete blocked."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")

    if user.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Cannot delete superadmin account.")

    username = user.username
    user_role = user.role

    await db.delete(user)
    await db.flush()

    await log_activity(
        user_id=current_user.id,
        action="user_deleted",
        details={"deleted_username": username, "deleted_role": user_role},
    )

    return APIResponse.ok(data={"deleted": username, "message": f"User '{username}' permanently deleted."})
