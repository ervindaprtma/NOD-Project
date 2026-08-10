"""
Central configuration loaded from environment variables via .env.
All settings are typed and validated via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Service ports ──────────────────────────────────────────
    POSTGRES_PORT: int = 5432

    # ── Frontend ───────────────────────────────────────────────
    NEXT_PUBLIC_API_BASE_URL: str = "http://localhost:80"

    # ── Security ───────────────────────────────────────────────
    JWT_SECRET: str = ""

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters long")
        return v

    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # 24h idle timeout — session invalidates after this if no auto-refresh
    # Active users (auto-refresh running) get continuously extended session
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours — idle timeout (no code references SESSION_INACTIVITY_TIMEOUT)
    MAX_SESSIONS_PER_USER: int = 5  # max concurrent active refresh tokens
    ALLOWED_ORIGINS: str = "http://localhost:80"

    @property
    def allowed_origins_list(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    # ── Database ───────────────────────────────────────────────
    POSTGRES_DB: str = "nod_db"
    POSTGRES_USER: str = "nod_user"
    POSTGRES_PASSWORD: str = ""
    DATABASE_URL: str = "postgresql+asyncpg://nod_user:changeme@db:5432/nod_db"

    # ── OpenSearch ─────────────────────────────────────────────
    OPENSEARCH_DC_URL: str = "http://10.80.150.108:9200"
    OPENSEARCH_DRC_URL: str = "http://10.90.150.108:9200"
    OPENSEARCH_IPSEC_URL: str = "http://10.90.150.108:9200"
    OPENSEARCH_USERNAME: str = ""
    OPENSEARCH_PASSWORD: str = ""
    OPENSEARCH_POOL_SIZE: int = 10
    OPENSEARCH_REQUEST_TIMEOUT: int = 30
    OPENSEARCH_QUERY_TIMEOUT: int = 120  # per-query timeout for aggregations (120s for 24h+ ranges)
    OPENSEARCH_VERIFY_CERTS: bool = False  # set True + mount CA cert for production
    OPENSEARCH_CA_CERT_PATH: str = ""  # path to CA cert inside container when verify=True

    # ── Site configuration (Q-06 compliance) ───────────────────
    TELEGRAF_SDWAN_SITES: str = "Site_FGT-DC,Site_FGT-DRC,Site_FGT_Office"
    TELEGRAF_SSLVPN_SITES: str = "Site_FGT-DC_SSLVPN,Site_FGT-DRC_SSLVPN"

    @property
    def sdwan_sites_list(self) -> List[str]:
        return [s.strip() for s in self.TELEGRAF_SDWAN_SITES.split(",") if s.strip()]

    @property
    def sslvpn_sites_list(self) -> List[str]:
        return [s.strip() for s in self.TELEGRAF_SSLVPN_SITES.split(",") if s.strip()]

    # ── Alert engine ───────────────────────────────────────────
    ALERT_POLL_INTERVAL_SECONDS: int = 60
    ALERT_RENOTIFY_INTERVAL_MINUTES: int = 30
    # Anti-flap: a FIRING rule must read clear continuously for this many minutes
    # before it RESOLVES. Absorbs bursty metrics (per-app scan speed) that otherwise
    # flap fire↔resolve every tick. 0 disables (resolve on the first clear tick).
    ALERT_RESOLVE_HYSTERESIS_MINUTES: int = 2

    # ── Notification: Email ────────────────────────────────────
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM_ADDRESS: str = ""

    # ── Notification: Telegram ─────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ── Notification: WhatsApp ─────────────────────────────────
    WHATSAPP_API_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""

    # ── Notification: Discord ──────────────────────────────────
    DISCORD_WEBHOOK_URL: str = ""

    # ── Logging ────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_MAX_BYTES: int = 104_857_600  # 100 MB
    LOG_BACKUP_COUNT: int = 10
    SYSLOG_ENABLED: bool = False
    SYSLOG_HOST: str = ""
    SYSLOG_PORT: int = 514

    # ── System Logs (queryable DB sink — LOGGING_SYSTEM_DESIGN) ─
    SYSTEM_LOG_ENABLED: bool = True
    SYSTEM_LOG_QUEUE_MAX: int = 10_000          # bounded ring; drops oldest on overflow
    SYSTEM_LOG_FLUSH_SECONDS: float = 2.0       # batch-write cadence
    SYSTEM_LOG_FLUSH_MAX_ROWS: int = 500        # or flush early at this many queued
    SYSTEM_LOG_INFO_RETENTION_DAYS: int = 7     # chatty success logs — shortest
    SYSTEM_LOG_RETENTION_DAYS: int = 30         # WARNING
    SYSTEM_LOG_AUDIT_RETENTION_DAYS: int = 90   # ALERT + ERROR — audit/failure trail lives longest
    SYSTEM_LOG_CAPTURE_INFO_REQUESTS: bool = True  # persist every successful API call

    # ── Timeframe defaults ─────────────────────────────────────
    DEFAULT_REFRESH_INTERVAL_SECONDS: int = 60

    # ── Rate limiting (P0 security) ──────────────────────────
    RATE_LIMIT_DEFAULT_REQUESTS: int = 120
    RATE_LIMIT_DEFAULT_WINDOW: str = "minute"
    RATE_LIMIT_LOGIN_REQUESTS: int = 10
    RATE_LIMIT_LOGIN_WINDOW: str = "minute"
    RATE_LIMIT_REFRESH_REQUESTS: int = 20
    RATE_LIMIT_REFRESH_WINDOW: str = "minute"

    # ── App config ──────────────────────────────────────────────
    ENVIRONMENT: str = "production"  # set "development" to enable /api/docs


@lru_cache()
def get_settings() -> Settings:
    return Settings()
