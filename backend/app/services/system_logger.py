"""
System logging service (LOGGING_SYSTEM_DESIGN §3-4).

One non-blocking write path for the queryable `system_logs` sink:
`log_event(...)` redacts + enqueues to a bounded ring; a background batch writer
drains it into Postgres. Logging must NEVER raise into or block a caller, and
credentials must NEVER be stored — `_redact` runs on every message/details.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import insert

from app.core.config import get_settings
from app.db.models import SystemLog
from app.db.session import AsyncSessionLocal

settings = get_settings()
logger = logging.getLogger(__name__)

VALID_LEVELS = ("INFO", "ALERT", "ERROR", "WARNING")

# ── Redaction ────────────────────────────────────────────────────
# Keys whose VALUES are never stored (case-insensitive substring match).
_SENSITIVE_KEYS = (
    "password", "passwd", "token", "bot_token", "secret", "authorization",
    "api_key", "apikey", "refresh_token", "access_token", "cookie",
    "set-cookie", "private_key", "chat_id",
)
# Value patterns scrubbed anywhere inside strings.
_VALUE_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"),  # JWT
    re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}\b"),                                    # Telegram bot token
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S+"),       # k=v secrets
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),                                    # basic-auth in URL
]
_REDACTED = "***"


def _redact(text: Any) -> str:
    """Scrub secret-looking values out of a string. Never raises."""
    try:
        s = str(text)
        for pat in _VALUE_PATTERNS:
            s = pat.sub(_REDACTED, s)
        return s
    except Exception:
        return ""


def _redact_dict(d: Any, _depth: int = 0) -> Any:
    """Recursively redact a details payload: sensitive keys → ***; scrub string values."""
    if d is None or _depth > 6:
        return None if d is None else _REDACTED
    try:
        if isinstance(d, dict):
            out: dict[str, Any] = {}
            for k, v in d.items():
                kl = str(k).lower()
                if any(sk in kl for sk in _SENSITIVE_KEYS):
                    out[str(k)] = _REDACTED
                else:
                    out[str(k)] = _redact_dict(v, _depth + 1)
            return out
        if isinstance(d, (list, tuple)):
            return [_redact_dict(v, _depth + 1) for v in d][:200]
        if isinstance(d, str):
            return _redact(d)
        if isinstance(d, (int, float, bool)):
            return d
        return _redact(d)
    except Exception:
        return _REDACTED


# ── Bounded ring + counters ──────────────────────────────────────
_QUEUE_MAX: int = max(1000, settings.SYSTEM_LOG_QUEUE_MAX)
_queue: deque[dict] = deque(maxlen=_QUEUE_MAX)
_dropped = 0
_written = 0
_writer_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex


def log_event(
    *,
    level: str,
    category: str,
    event: str,
    message: str,
    source: str = "backend",
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    source_ip: Optional[str] = None,
    trace_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    """Redact + enqueue one row. Sync, non-blocking, never raises.

    The batch writer persists it. On overflow the oldest row is dropped and the
    drop is counted (surfaced via /stats) — never silent.
    """
    global _dropped
    if not settings.SYSTEM_LOG_ENABLED:
        return
    try:
        lvl = level if level in VALID_LEVELS else "INFO"
        # Path never carries a query string (can hold tokens).
        p = path.split("?", 1)[0] if path else None
        row = {
            "id": _new_id(),
            "ts": datetime.now(timezone.utc),
            "level": lvl,
            "category": (category or "system")[:20],
            "source": "frontend" if source == "frontend" else "backend",
            "event": (event or "log")[:60],
            "message": _redact(message)[:8000],
            "username": (str(username)[:150] if username else None),
            "user_id": user_id,
            "source_ip": (str(source_ip)[:45] if source_ip else None),
            "trace_id": (str(trace_id)[:64] if trace_id else None),
            "rule_id": rule_id,
            "method": (str(method)[:8] if method else None),
            "path": (p[:255] if p else None),
            "status_code": status_code,
            "duration_ms": duration_ms,
            "details": _redact_dict(details),
        }
        if len(_queue) >= _QUEUE_MAX:  # about to drop the oldest
            _dropped += 1
        _queue.append(row)  # deque(maxlen) evicts oldest automatically
    except Exception:
        # Logging must never crash the caller.
        pass


def _drain(max_n: int) -> list[dict]:
    rows: list[dict] = []
    while _queue and len(rows) < max_n:
        try:
            rows.append(_queue.popleft())
        except IndexError:
            break
    return rows


async def _flush_once() -> None:
    global _written
    rows = _drain(max(1, settings.SYSTEM_LOG_FLUSH_MAX_ROWS) * 8)
    if not rows:
        return
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(insert(SystemLog), rows)
            await session.commit()
        _written += len(rows)
    except Exception as e:
        # Don't re-enqueue (would spin if the DB is down) and don't route through
        # logger.error (the DB handler ignores this module, but stay quiet anyway).
        logger.warning("system_logger flush failed (%d rows dropped): %s", len(rows), e)


async def _writer_loop() -> None:
    interval = max(0.5, settings.SYSTEM_LOG_FLUSH_SECONDS)
    while not _stop.is_set():
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        await _flush_once()
    await _flush_once()  # final drain on shutdown


def start_system_logger() -> None:
    """Launch the background batch writer (call once from lifespan startup)."""
    global _writer_task
    if _writer_task is not None or not settings.SYSTEM_LOG_ENABLED:
        return
    _stop.clear()
    _writer_task = asyncio.create_task(_writer_loop())


async def stop_system_logger() -> None:
    """Signal the writer to drain and stop (call from lifespan shutdown)."""
    global _writer_task
    if _writer_task is None:
        return
    _stop.set()
    try:
        await asyncio.wait_for(_writer_task, timeout=5)
    except Exception:
        pass
    _writer_task = None


def get_logger_stats() -> dict[str, int]:
    return {"queued": len(_queue), "dropped_rows": _dropped, "written": _written}


async def prune_system_logs() -> int:
    """Delete rows past their per-level retention. Returns rows removed."""
    from sqlalchemy import delete, or_, and_

    now = datetime.now(timezone.utc)
    info_cut = now - timedelta(days=settings.SYSTEM_LOG_INFO_RETENTION_DAYS)
    warn_cut = now - timedelta(days=settings.SYSTEM_LOG_RETENTION_DAYS)
    audit_cut = now - timedelta(days=settings.SYSTEM_LOG_AUDIT_RETENTION_DAYS)
    try:
        async with AsyncSessionLocal() as session:
            stmt = delete(SystemLog).where(
                or_(
                    and_(SystemLog.level == "INFO", SystemLog.ts < info_cut),
                    and_(SystemLog.level == "WARNING", SystemLog.ts < warn_cut),
                    and_(SystemLog.level.in_(("ALERT", "ERROR")), SystemLog.ts < audit_cut),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0
    except Exception as e:
        logger.warning("system_logs prune failed: %s", e)
        return 0
