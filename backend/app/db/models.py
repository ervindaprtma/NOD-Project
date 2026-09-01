"""
SQLAlchemy ORM models for the NOD application database.
All tables are managed via Alembic migrations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return uuid.uuid4().hex


# ─────────────────────────────────────────────────────────────────
# Users & Authentication
# ─────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    username: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="viewer"
    )  # superadmin, admin, operator, viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    preferences: Mapped[Optional["UserPreference"]] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_ip: Mapped[Optional[str]] = mapped_column(
        String(45), nullable=True, comment="IP address of client that created this token"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


# ─────────────────────────────────────────────────────────────────
# Alert System
# ─────────────────────────────────────────────────────────────────


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # INFO, WARNING, CRITICAL
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="single", server_default=text("'single'")
    )  # single | composite (P5)
    notify_when: Mapped[str] = mapped_column(
        String(4), nullable=False, default="any", server_default=text("'any'")
    )  # any | all (composite rule combination logic, P5)
    data_source: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # appid_flow, sdwan_sla, ha_resource, vpn_ssl, vpn_ipsec, interface_stats
    metric_field: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sub-entity selector (Phase E): for interface_stats holds the ifIndex (e.g. "3").
    # Null for sources that address a single metric or encode the sub-entity in
    # metric_field (sdwan link, traffic path).
    target_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # interface_stats only: operator-entered link max (Mbps). When set, the % threshold is
    # stored as Mbps (threshold_value = link_max × %); this remembers the max so the UI can
    # show the % again on edit. Null = absolute-Mbps rule. Eval is unchanged (Mbps compare).
    link_max_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # appid_flow only: narrow the path metric to a specific application / protocol / dest port.
    # {"app": "YouTube", "protocol": "TCP", "port": 443} — any subset; null = whole path.
    appid_filter: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    aggregation: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # avg, max, min, sum, count
    condition: Mapped[str] = mapped_column(
        String(4), nullable=False
    )  # >, <, >=, <=, ==
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    evaluation_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    sustained_for_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Per-rule re-notify cadence while FIRING (minutes). NULL = inherit the global
    # ALERT_RENOTIFY_INTERVAL_MINUTES; 0 = notify once (no reminders); N>0 = every N min.
    renotify_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notify_channels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    clauses: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="Composite rule clauses (P5). List of {\"data_source\", \"metric_field\", ...} dicts.",
    )
    template_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("alert_templates.id", ondelete="SET NULL"), nullable=True
    )
    notification_template_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("notification_templates.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    site_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    template: Mapped[Optional["AlertTemplate"]] = relationship(back_populates="rules")
    notification_template: Mapped[Optional["NotificationTemplate"]] = relationship(back_populates="rules")


class AlertTemplate(Base):
    """Pre-built rule templates for non-technical users (v3 §3.12).

    A template hardcodes locked_fields (data_source, threshold, etc.) and
    exposes only a few fields (e.g. threshold_value) via exposed_fields.
    This allows non-technical users to create rules by picking a template
    and filling in a few values, without needing to understand data sources
    or aggregation functions.
    """
    __tablename__ = "alert_templates"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="performance", index=True
    )  # availability, performance, security, capacity
    icon: Mapped[str] = mapped_column(String(4), nullable=False, default="📊")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject_template: Mapped[str] = mapped_column(Text, nullable=False, default="Alert: {{ name }}")
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    underlying_kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="single"
    )  # single | composite
    locked_fields: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Fields hardcoded by this template (data_source, metric_field, etc.)"
    )
    exposed_fields: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="Fields the user can set (e.g. ['threshold_value', 'site_name'])"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_user_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    rules: Mapped[list["AlertRule"]] = relationship(back_populates="template")


class NotificationTemplate(Base):
    """Message-format templates for alert notifications (§11.1).

    Split from AlertTemplate — this owns subject/body/line templates only.
    Rule-shape templates (data_source, metric_field, etc.) stay in AlertTemplate.
    """
    __tablename__ = "notification_templates"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject_template: Mapped[str] = mapped_column(
        Text, nullable=False, default="Alert: {{ rule.name }}"
    )
    body_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    line_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Inactive templates aren't offered to rules and aren't rendered — the engine falls
    # back to the default (or hardcoded) line. Lets an admin retire a template without
    # deleting it or breaking rules that still reference it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    is_user_created: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    rules: Mapped[list["AlertRule"]] = relationship(back_populates="notification_template")


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    # SET NULL (not CASCADE): deleting a rule must NOT erase its history — the row
    # keeps the fire-time rule_name and event_code; rule_id becomes NULL.
    rule_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    metric_value_at_firing: Mapped[float] = mapped_column(Float, nullable=False)
    notified_channels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rule_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Stable, human-quotable per-event id: AH-<YYYYMMDD>-<8 hex of sha256(values)>.
    event_code: Mapped[Optional[str]] = mapped_column(String(24), nullable=True, index=True)
    # NULL for threshold rules (Firing/Resolved derived from resolved_at). Point events
    # carry their own name: "connected" | "disconnected" (VPN session) | "rebooted" (device).
    event_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # The exact subject/body sent per channel, for firing and resolved, so the
    # history row shows what the operator actually received. {"firing": {...}, "resolved": {...}}.
    sent_payloads: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class AlertState(Base):
    """Tracks the current evaluation state of each alert rule (in-memory cache alternative)."""
    __tablename__ = "alert_states"

    rule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("alert_rules.id", ondelete="CASCADE"),
        primary_key=True, nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="INACTIVE"
    )  # INACTIVE, PENDING, FIRING, RESOLVED
    pending_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_fired_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ── Phase C: evaluation observability (stamped every tick / transition) ──
    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # last tick that touched this rule
    last_value: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # metric value from that evaluation
    last_state_change_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # when it entered its current state (for "FIRING for 4m")
    last_read_degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )  # last read was held (OpenSearch degraded) — "data delayed" badge
    # kind="session" rules only: the previous poll's active VPN sessions, so the next poll can
    # diff for connect/disconnect. {username: {remote_ip, active_ip, started_at, device}}.
    session_state: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )


class AlertFieldCatalog(Base):
    """Field catalog for guided rule creation (§11.2).

    Single source of truth for data_source, field_key, units, and valid
    aggregations/conditions. Used by Field Reference tab and template-driven
    rule creation.
    """
    __tablename__ = "alert_field_catalog"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    data_source: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # ha_resource, sdwan_sla, vpn_ssl, vpn_ipsec, appid_flow
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    category: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # state | traffic
    valid_aggregations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )  # e.g. ["avg", "max"]
    valid_conditions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )  # e.g. [">", "<", ">=", "<="]
    example_threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("data_source", "field_key", name="uq_field_catalog"),
    )


class NotificationConfig(Base):
    """Configurations → Notifications (v3 §3.13).

    Encrypted credentials for WhatsApp/Telegram/SMTP notification channels.
    Secrets are encrypted at rest via Fernet (core/security.py).
    GET returns masked values; only an explicit PUT with new values changes them.
    """
    __tablename__ = "notification_configs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    channel: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )  # whatsapp, telegram, smtp
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    min_severity: Mapped[str] = mapped_column(
        String(20), default="CRITICAL", nullable=False
    )  # INFO, WARNING, CRITICAL
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="Encrypted credentials + plaintext metadata"
    )
    recipients: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="Optional per-group routing (e.g. DC alerts → DC group)"
    )
    updated_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )


# ─────────────────────────────────────────────────────────────────
# User Activity & Notifications
# ─────────────────────────────────────────────────────────────────


class UserActivityLog(Base):
    __tablename__ = "user_activity_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    source_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True
    )


class SystemLog(Base):
    """Queryable operational log (LOGGING_SYSTEM_DESIGN §2).

    Beside the file/stdout pipeline — the DB copy is what the admin System Logs
    page reads. Four buckets via `level` (INFO|ALERT|ERROR|WARNING); `category`
    sub-filters. Credentials are NEVER stored (redacted on write); `username` is
    always kept so every row answers "who".
    """
    __tablename__ = "system_logs"
    # Composite index for the default tabbed view (filter by level, newest first).
    # Declared here so `alembic check` sees it — must match the migration exactly.
    __table_args__ = (
        Index("ix_system_logs_level_ts", "level", "ts"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # INFO|ALERT|ERROR|WARNING
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # backend|frontend
    event: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(150), nullable=True, index=True)
    # No FK cascade: the audit trail must survive user deletion (username is denormalized).
    user_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), nullable=True)
    source_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    rule_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alert_log_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("alert_logs.id", ondelete="CASCADE"), nullable=True
    )
    alert_name: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# ─────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────


class ReportJob(Base):
    __tablename__ = "report_jobs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    report_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # R-01, R-02, R-03, R-04
    output_format: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # pdf, html, docx
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, running, completed, failed, expired
    # Nullable: ondelete=SET NULL cannot fire against a NOT NULL column — deleting any
    # user who ever ran a report raised NotNullViolation instead. The job outlives its
    # creator on purpose; an orphaned row is the intended end state.
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    time_range_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    time_range_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="True if file was deleted from storage")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sites: Mapped[Optional[list[str]]] = mapped_column(
        JSONB, nullable=True,
        comment="List of site names to include in report"
    )
    sections: Mapped[Optional[list[str]]] = mapped_column(
        JSONB, nullable=True,
        comment="List of report sections to include; None = all"
    )
    table_interval: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
        comment="R-09 only: interval for detail table rows (15m/30m/1h/2h/4h/6h/12h/24h)"
    )
    sla_thresholds: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="R-04 only: {wan:{latency,jitter,packet_loss}, mpls:{...}} SLA ceilings"
    )


# ─────────────────────────────────────────────────────────────────
# Scheduled Reports
# ─────────────────────────────────────────────────────────────────

class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    output_format: Mapped[str] = mapped_column(String(10), nullable=False, default="html")
    cron_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    sites: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    sections: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    channels: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array
    recipient_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recipient_phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Nullable for the same reason as report_jobs.created_by — see there.
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


# ─────────────────────────────────────────────────────────────────
# User Preferences
# ─────────────────────────────────────────────────────────────────


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True, nullable=False
    )
    theme: Mapped[str] = mapped_column(String(10), default="light", nullable=False)  # light, dark
    alert_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="preferences")


class UserPinnedWidget(Base):
    __tablename__ = "user_pinned_widgets"
    __table_args__ = (
        UniqueConstraint("user_id", "widget_id", name="uq_user_widget"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    widget_id: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. P01-A, TF-01
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class MaintenanceWindow(Base):
    """Planned maintenance window suppressing alerts for a site (v3 §3.14).

    During the window, alert rules matching the site are NOT evaluated —
    they're skipped silently (no FIRING, no RESOLVED).
    """
    __tablename__ = "maintenance_windows"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_new_uuid
    )
    site_name: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class DeviceIpAlias(Base):
    """Re-IPed device mapping: old tag.source IP → current tag.source IP.

    Availability identity is tag.source (IP); when a device is re-IPed the old
    era's docs would orphan and the new IP would show as a fresh device with
    partial_history. An explicit alias row lets the roster query include both
    IPs so eras stitch under one device card. Superadmin-managed (explicit
    mapping, never auto-inferred from hostname).
    """
    __tablename__ = "device_ip_aliases"

    old_ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    current_ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
