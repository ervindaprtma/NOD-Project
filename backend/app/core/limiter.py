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


def get_real_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For (set by Nginx).
    Falls back to X-Real-IP, then direct connection IP if no proxy header present.

    Use this instead of request.client.host — behind nginx, request.client.host
    returns the Docker bridge IP (e.g. 172.18.0.5), not the user's real IP.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For: client, proxy1, proxy2 — take first (client)
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_real_client_ip,
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
