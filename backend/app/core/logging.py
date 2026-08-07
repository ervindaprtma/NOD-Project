"""
Structured application logging.
Writes JSON-formatted logs to access.log and error.log with trace_id correlation.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler, SysLogHandler
from pathlib import Path

from pythonjsonlogger import jsonlogger

from app.core.config import get_settings

settings = get_settings()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


class JsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        # trace_id is injected by middleware; fallback to "no-trace"
        if "trace_id" not in log_record:
            log_record["trace_id"] = getattr(record, "trace_id", "no-trace")


def _create_rotating_handler(path: Path) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        str(path),
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
    )
    handler.setFormatter(JsonFormatter())
    return handler


# Message-prefix → (category, event) mapping so auto-captured logs get a useful
# code instead of a generic one. Best-effort; explicit log_event() calls at the
# high-value sites still override these with structured fields.
_EVENT_HINTS = [
    ("batch notify failed", ("notify", "notify.flush_failed")),
    ("send failed", ("notify", "notify.send_failed")),
    ("template render failed", ("alert", "template.render_failed")),
    ("session fetch failed", ("query", "query.failed")),
    ("opensearch", ("query", "query.failed")),
    ("query", ("query", "query.failed")),
    ("decrypt", ("notify", "notify.decrypt_failed")),
    ("login", ("auth", "auth.login_failed")),
]


class SystemLogDBHandler(logging.Handler):
    """Mirror WARNING+ log records into the queryable `system_logs` sink.

    Enqueue-only (the batch writer persists) so it never blocks or touches the DB
    inside a logging call. Records from the system_logger module are skipped to
    prevent a write-failure → log → write feedback loop.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith("app.services.system_logger"):
                return
            from app.services.system_logger import log_event

            level = "ERROR" if record.levelno >= logging.ERROR else "WARNING"
            msg = record.getMessage()
            low = msg.lower()
            category, event = "system", "log.captured"
            for needle, (cat, ev) in _EVENT_HINTS:
                if needle in low:
                    category, event = cat, ev
                    break
            log_event(
                level=level,
                category=category,
                event=event,
                message=msg,
                trace_id=getattr(record, "trace_id", None),
                details={"logger": record.name},
            )
        except Exception:
            pass  # a logging handler must never raise


def setup_logging() -> None:
    """Configure the application root logger."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    root.handlers.clear()

    # Access log
    access_handler = _create_rotating_handler(LOG_DIR / "access.log")
    access_handler.addFilter(_AccessLogFilter())
    root.addHandler(access_handler)

    # Error log
    error_handler = _create_rotating_handler(LOG_DIR / "error.log")
    error_handler.setLevel(logging.WARNING)
    root.addHandler(error_handler)

    # Queryable DB sink for WARNING+ (System Logs page). Enqueue-only; the batch
    # writer (started in lifespan) drains it. Safe even before the writer starts.
    if settings.SYSTEM_LOG_ENABLED:
        db_handler = SystemLogDBHandler()
        db_handler.setLevel(logging.WARNING)
        root.addHandler(db_handler)

    # Console (for Docker log driver)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JsonFormatter())
    root.addHandler(console_handler)

    # Optional syslog forwarding
    if settings.SYSLOG_ENABLED and settings.SYSLOG_HOST:
        syslog_handler = SysLogHandler(
            address=(settings.SYSLOG_HOST, settings.SYSLOG_PORT),
        )
        syslog_handler.setFormatter(JsonFormatter())
        root.addHandler(syslog_handler)

    # Silence noisy libs
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("opensearch").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.WARNING)


class _AccessLogFilter(logging.Filter):
    """Only allow INFO-level access log messages into access.log."""
    def filter(self, record):
        return record.levelno == logging.INFO and hasattr(record, "trace_id")
