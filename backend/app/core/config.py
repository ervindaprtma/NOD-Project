"""
Central configuration loaded from environment variables via .env.
All settings are typed and validated via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Service ports ──────────────────────────────────────────
    NGINX_PORT: int = 80
    FRONTEND_PORT: int = 3000
    BACKEND_PORT: int = 8000
    POSTGRES_PORT: int = 5432

    # ── Frontend ───────────────────────────────────────────────
    NEXT_PUBLIC_API_BASE_URL: str = "http://localhost:80"

    # ── Security ───────────────────────────────────────────────
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_HOURS: int = 24
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
    OPENSEARCH_REQUEST_TIMEOUT: int = 10

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

    # ── Timeframe defaults ─────────────────────────────────────
    DEFAULT_REFRESH_INTERVAL_SECONDS: int = 60
    SESSION_IDLE_TIMEOUT_HOURS: int = 4

    # ── Optional features ──────────────────────────────────────
    METRICS_ENABLED: bool = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
