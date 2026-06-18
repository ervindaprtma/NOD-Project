"""
Security utilities: JWT token creation/validation, bcrypt password hashing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# bcrypt cost factor >= 12 (per NFR 7.4)
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def create_access_token(
    subject: str,
    extra_claims: Optional[dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a JWT access token with configurable expiry."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    to_encode: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_delta,
        "jti": uuid4().hex,
        "type": "access",
    }
    if extra_claims:
        to_encode.update(extra_claims)

    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    subject: str,
    jti: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str, datetime]:
    """Create a JWT refresh token. Returns (token, jti, expires_at)."""
    if expires_delta is None:
        expires_delta = timedelta(hours=settings.REFRESH_TOKEN_EXPIRE_HOURS)

    if jti is None:
        jti = uuid4().hex

    now = datetime.now(timezone.utc)
    expires_at = now + expires_delta
    to_encode: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": expires_at,
        "jti": jti,
        "type": "refresh",
    }
    token = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT token.
    Raises JWTError on invalid/expired token.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "jti", "type"]},
        )
        return payload
    except JWTError:
        raise


def decode_token_optional(token: str) -> Optional[dict[str, Any]]:
    """Decode without raising; returns None on failure."""
    try:
        return decode_token(token)
    except JWTError:
        return None
