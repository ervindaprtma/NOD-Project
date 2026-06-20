"""
Centralised rate-limiting configuration using slowapi (backed by in-memory storage).
Import and attach to the app and individual endpoints as needed.

P0 security fix: brute-force protection for auth endpoints and general API throttling.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings

settings = get_settings()

# ── Global limiter instance ────────────────────────────────────
# Uses client IP by default. Override key_func per-endpoint when needed.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[
        f"{settings.RATE_LIMIT_DEFAULT_REQUESTS}/{settings.RATE_LIMIT_DEFAULT_WINDOW}",
    ],
    storage_uri="memory://",
)


# ── Custom error handler ───────────────────────────────────────
def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a clean 429 JSON response when a rate limit is hit."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Please slow down.",
            "retry_after": getattr(exc, "retry_after", None),
        },
    )
