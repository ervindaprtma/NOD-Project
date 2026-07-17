"""
User and authentication schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _clean_email(v: str) -> str:
    """Strip surrounding whitespace and sanity-check shape.

    Deliberately not EmailStr: these are internal addresses and the strict RFC
    validator rejects some of them. This only catches the copy-paste damage that
    would otherwise be stored verbatim — a trailing space makes an address that
    looks identical in the UI but never matches a lookup.
    """
    v = v.strip()
    if "@" not in v or any(c.isspace() for c in v):
        raise ValueError("Email must contain '@' and no spaces")
    return v


def _validate_password_complexity(v: str) -> str:
    """Enforce min 8 chars with uppercase, lowercase, digit, special char."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not any(c.islower() for c in v):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(c.isupper() for c in v):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one digit")
    if not any(c in "@$!%*?&^#()_+=-{}[]:;\"'<>,.?/~`|\\" for c in v):
        raise ValueError("Password must contain at least one special character")
    return v


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

    @field_validator("new_password")
    @classmethod
    def _check_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)


# ── User CRUD ───────────────────────────────────────────────────


class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    email: str = Field(..., min_length=3, max_length=255)  # internal env — no EmailStr validation
    full_name: str = Field(default="", max_length=255)
    role: str = Field(default="viewer", pattern=r"^(superadmin|admin|operator|viewer)$")

    @field_validator("email")
    @classmethod
    def _clean(cls, v: str) -> str:
        return _clean_email(v)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, v: str) -> str:
        # min_length runs before this validator, so a whitespace-only username would
        # pass the constraint and then strip down to "".
        v = v.strip()
        if not v:
            raise ValueError("Username cannot be blank")
        return v


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_complexity(cls, v: str) -> str:
        return _validate_password_complexity(v)


class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=1, max_length=128)
    email: Optional[str] = Field(default=None, min_length=3, max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[str] = Field(default=None, pattern=r"^(superadmin|admin|operator|viewer)$")
    is_active: Optional[bool] = None
    must_change_password: Optional[bool] = None
    # Admin-set password reset. Self-service goes through /me/password, which
    # requires the current password; this path deliberately does not.
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _check_complexity(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _validate_password_complexity(v)

    @field_validator("email")
    @classmethod
    def _clean(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _clean_email(v)

    @field_validator("username")
    @classmethod
    def _strip_username(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("Username cannot be blank")
        return v


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
