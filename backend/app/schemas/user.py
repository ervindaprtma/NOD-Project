"""
User and authentication schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Auth ────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Refresh token is read from HTTP-only cookie, not body."""
    pass


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


# ── User CRUD ───────────────────────────────────────────────────


class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    email: str = Field(..., min_length=3, max_length=255)  # internal env — no EmailStr validation
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default="viewer", pattern=r"^(superadmin|admin|operator|viewer)$")


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)


class UserUpdate(BaseModel):
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, pattern=r"^(superadmin|admin|operator|viewer)$")
    is_active: Optional[bool] = None
    must_change_password: Optional[bool] = None


class UserRead(UserBase):
    id: str
    is_active: bool
    must_change_password: bool
    last_login: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    users: list[UserRead]
    total: int
