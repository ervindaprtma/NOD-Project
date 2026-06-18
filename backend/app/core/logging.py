"""
Structured application logging.
Writes JSON-formatted logs to access.log and error.log with trace_id correlation.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler, SysLogHandler
from pathlib import Path
from uuid import uuid4

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
