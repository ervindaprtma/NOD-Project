"""
Authentication endpoints: login, logout, refresh.
Also defines FastAPI dependencies for JWT validation and role-based access control.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_optional,
    hash_password,
    verify_password,
)
from app.db.models import RefreshToken as RefreshTokenModel
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.user import ChangePasswordRequest, LoginRequest, TokenResponse, UserRead
from app.services.activity_logger import log_activity

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Rate limiter — import from core module
from app.core.limiter import limiter

# Role hierarchy for RBAC
_ROLE_HIERARCHY = {
    "viewer": 0,
    "operator": 1,
    "admin": 2,
    "superadmin": 3,
}

COOKIE_REFRESH_TOKEN = "nod_refresh_token"


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────


@router.post("/login", response_model=APIResponse[TokenResponse])
@limiter.limit(f"{settings.RATE_LIMIT_LOGIN_REQUESTS}/{settings.RATE_LIMIT_LOGIN_WINDOW}")
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user, return access token and set refresh token cookie."""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact administrator.",
        )

    # Update last_login
    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    # Log activity (fire-and-forget)
    asyncio.ensure_future(
        log_activity(
            user_id=user.id,
            action="login",
            source_ip=request.client.host if request.client else None,
        )
    )

    # Issue tokens
    access_token = create_access_token(
        subject=user.id,
        extra_claims={"role": user.role, "username": user.username},
    )
    refresh_token, jti, expires_at = create_refresh_token(subject=user.id)

    # Persist refresh token
    db.add(
        RefreshTokenModel(
            user_id=user.id,
            jti=jti,
            expires_at=expires_at,
        )
    )
    await db.flush()

    response_data = APIResponse.ok(TokenResponse(access_token=access_token))

    # Set refresh token as HTTP-only cookie
    resp = Response(
        content=response_data.model_dump_json(),
        media_type="application/json",
    )
    # Detect if the incoming request is HTTPS; if not, set secure=False for dev
    is_https = request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(
        key=COOKIE_REFRESH_TOKEN,
        value=refresh_token,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=int(settings.REFRESH_TOKEN_EXPIRE_HOURS * 3600),
        path="/auth",
    )
    return resp


@router.post("/logout", response_model=APIResponse[dict])
async def logout(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None, alias=COOKIE_REFRESH_TOKEN),
    db: AsyncSession = Depends(get_db),
):
    """Revoke refresh token and clear cookie."""
    if refresh_token:
        payload = decode_token_optional(refresh_token)
        user_id = None
        if payload:
            jti = payload.get("jti")
            user_id = payload.get("sub")
            result = await db.execute(
                select(RefreshTokenModel).where(RefreshTokenModel.jti == jti)
            )
            token_record = result.scalar_one_or_none()
            if token_record:
                token_record.is_revoked = True
                await db.flush()
        # Log logout (await to ensure it completes before response)
        if user_id:
            await log_activity(
                user_id=user_id,
                action="logout",
                source_ip=request.client.host if request.client else None,
            )

    resp = Response(
        content=APIResponse.ok({"message": "Logged out"}).model_dump_json(),
        media_type="application/json",
    )
    resp.delete_cookie(COOKIE_REFRESH_TOKEN, path="/auth")
    return resp


@router.post("/refresh", response_model=APIResponse[TokenResponse])
@limiter.limit(f"{settings.RATE_LIMIT_REFRESH_REQUESTS}/{settings.RATE_LIMIT_REFRESH_WINDOW}")
async def refresh(
    request: Request,
    refresh_token: Optional[str] = Cookie(default=None, alias=COOKIE_REFRESH_TOKEN),
    db: AsyncSession = Depends(get_db),
):
    """Issue a new access token using a valid refresh token cookie."""
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing.",
        )

    payload = decode_token_optional(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    user_id = payload.get("sub")
    jti = payload.get("jti")

    # Verify refresh token in DB (not revoked)
    result = await db.execute(
        select(RefreshTokenModel).where(RefreshTokenModel.jti == jti)
    )
    token_record = result.scalar_one_or_none()

    if not token_record or token_record.is_revoked or token_record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalid or expired.",
        )

    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )

    # Rotate: revoke old token and issue new refresh + access tokens
    token_record.is_revoked = True

    new_refresh_token, new_jti, new_expires_at = create_refresh_token(subject=user.id)
    db.add(
        RefreshTokenModel(
            user_id=user.id,
            jti=new_jti,
            expires_at=new_expires_at,
        )
    )

    access_token = create_access_token(
        subject=user.id,
        extra_claims={"role": user.role, "username": user.username},
    )

    await db.commit()

    response_data = APIResponse.ok(TokenResponse(access_token=access_token))
    resp = Response(
        content=response_data.model_dump_json(),
        media_type="application/json",
    )
    is_https = request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
    resp.set_cookie(
        key=COOKIE_REFRESH_TOKEN,
        value=new_refresh_token,
        httponly=True,
        secure=is_https,
        samesite="strict",
        max_age=int(settings.REFRESH_TOKEN_EXPIRE_HOURS * 3600),
        path="/auth",
    )
    return resp


# ─────────────────────────────────────────────────────────────────
# Dependencies (used by all protected routes)
# ─────────────────────────────────────────────────────────────────


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: extracts and validates JWT from Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header.",
        )

    token = auth_header.removeprefix("Bearer ").strip()
    payload = decode_token_optional(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated.",
        )

    # Enforce mandatory password change
    if getattr(user, "must_change_password", False):
        allowed_paths = {"/auth/logout", "/users/me/password"}
        if request.url.path not in allowed_paths:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "MUST_CHANGE_PASSWORD", "message": "Password change required"},
            )

    return user


def require_role(minimum_role: str):
    """
    FastAPI dependency factory: returns a dependency that enforces minimum role.
    Usage: Depends(require_role("admin"))
    """
    async def role_checker(current_user: User = Depends(get_current_user)):
        required_level = _ROLE_HIERARCHY.get(minimum_role, 0)
        user_level = _ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges.",
            )
        return current_user
    return role_checker
