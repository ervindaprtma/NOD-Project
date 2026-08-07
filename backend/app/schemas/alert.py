"""
Alert rule and alert log schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

_DATA_SOURCE_RE = r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec|interface_stats|device_uptime)$"


class AppidFilter(BaseModel):
    """appid_flow scoping — narrow a path metric to a specific application / protocol / port.
    Any subset; all-empty means the whole path. Fields map to flow.application.name,
    l4.proto.name, flow.server.l4.port.id. The *_not twins EXCLUDE (e.g. alert on all internet
    traffic except Windows-Update). JSONB column absorbs the new keys with no migration."""
    app: Optional[str] = Field(default=None, max_length=128)
    protocol: Optional[str] = Field(default=None, max_length=16)
    port: Optional[int] = Field(default=None, ge=0, le=65535)
    app_not: Optional[str] = Field(default=None, max_length=128)
    protocol_not: Optional[str] = Field(default=None, max_length=16)
    port_not: Optional[int] = Field(default=None, ge=0, le=65535)
    # Scan mode (metric_field "app.<path>.<metric>"): monitor ALL apps, fire on any over threshold.
    # top_n = apps ranked per tick; min_mbps = floor so tiny apps can't trip/flap the rule.
    top_n: Optional[int] = Field(default=None, ge=1, le=100)
    min_mbps: Optional[float] = Field(default=None, ge=0)


class AlertClause(BaseModel):
    """One clause of a composite rule. Evaluated independently, then combined by
    notify_when (all=AND / any=OR). target_key = interface ifIndex / device IP where the
    source needs one; evaluation_window_minutes defaults to the rule's window when omitted."""
    data_source: str = Field(..., pattern=_DATA_SOURCE_RE)
    metric_field: str = Field(..., min_length=1, max_length=255)
    aggregation: str = Field(default="avg", pattern=r"^(avg|max|min|sum|count)$")
    condition: str = Field(..., pattern=r"^(>|<|>=|<=|==)$")
    threshold_value: float
    target_key: Optional[str] = Field(default=None, max_length=64)
    evaluation_window_minutes: Optional[int] = Field(default=None, ge=1)


class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    severity: str = Field(..., pattern=r"^(INFO|WARNING|CRITICAL)$")
    data_source: str = Field(..., pattern=r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec|interface_stats|device_uptime)$")
    metric_field: str = Field(..., min_length=1, max_length=255)
    aggregation: str = Field(..., pattern=r"^(avg|max|min|sum|count)$")
    condition: str = Field(..., pattern=r"^(>|<|>=|<=|==)$")
    threshold_value: float
    evaluation_window_minutes: int = Field(..., ge=1)
    sustained_for_minutes: int = Field(..., ge=0)
    # Re-notify cadence while FIRING: None = inherit global default, 0 = notify once, N = every N min.
    renotify_interval_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    notify_channels: list[str] = Field(default_factory=list)
    notification_template_id: Optional[str] = None
    template_id: Optional[str] = None
    site_name: Optional[str] = Field(default=None, max_length=128)
    target_key: Optional[str] = Field(default=None, max_length=64)  # interface_stats ifIndex
    link_max_mbps: Optional[float] = Field(default=None, ge=0)  # interface_stats %-of-max mode
    appid_filter: Optional[AppidFilter] = None  # appid_flow app/protocol/port scoping
    kind: str = Field(default="single", pattern=r"^(single|composite|session|reboot)$")
    notify_when: str = Field(default="any", pattern=r"^(any|all)$")  # composite: OR / AND
    clauses: list[AlertClause] = Field(default_factory=list)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    severity: Optional[str] = Field(default=None, pattern=r"^(INFO|WARNING|CRITICAL)$")
    data_source: Optional[str] = Field(default=None, pattern=r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec|interface_stats|device_uptime)$")
    metric_field: Optional[str] = Field(default=None, min_length=1, max_length=255)
    aggregation: Optional[str] = Field(default=None, pattern=r"^(avg|max|min|sum|count)$")
    condition: Optional[str] = Field(default=None, pattern=r"^(>|<|>=|<=|==)$")
    threshold_value: Optional[float] = None
    evaluation_window_minutes: Optional[int] = Field(default=None, ge=1)
    sustained_for_minutes: Optional[int] = Field(default=None, ge=0)
    renotify_interval_minutes: Optional[int] = Field(default=None, ge=0, le=10080)
    notify_channels: Optional[list[str]] = None
    notification_template_id: Optional[str] = None
    template_id: Optional[str] = None
    site_name: Optional[str] = Field(default=None, max_length=128)
    target_key: Optional[str] = Field(default=None, max_length=64)  # interface_stats ifIndex
    link_max_mbps: Optional[float] = Field(default=None, ge=0)  # interface_stats %-of-max mode
    appid_filter: Optional[AppidFilter] = None  # appid_flow app/protocol/port scoping
    kind: Optional[str] = Field(default=None, pattern=r"^(single|composite|session|reboot)$")
    notify_when: Optional[str] = Field(default=None, pattern=r"^(any|all)$")
    clauses: Optional[list[AlertClause]] = None
    enabled: Optional[bool] = None


class AlertRuleRead(BaseModel):
    id: str
    name: str
    severity: str
    data_source: str
    metric_field: str
    target_key: Optional[str] = None
    link_max_mbps: Optional[float] = None
    appid_filter: Optional[dict] = None
    kind: str = "single"
    notify_when: str = "any"
    clauses: list[dict] = Field(default_factory=list)
    aggregation: str
    condition: str
    threshold_value: float
    evaluation_window_minutes: int
    sustained_for_minutes: int
    renotify_interval_minutes: Optional[int] = None
    notify_channels: list[str]
    template_id: Optional[str] = None
    notification_template_id: Optional[str] = None
    enabled: bool
    site_name: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # Live evaluation state from the state machine (INACTIVE/PENDING/FIRING/RESOLVED).
    # Populated by the list endpoint; None when no state row exists yet.
    state: Optional[str] = None
    # Phase C observability (from AlertState; None until first evaluation).
    last_evaluated_at: Optional[datetime] = None
    last_value: Optional[float] = None
    last_state_change_at: Optional[datetime] = None
    last_read_degraded: bool = False

    model_config = {"from_attributes": True}


class AlertTestClauseResult(BaseModel):
    """Per-clause row for a composite Test — every clause is evaluated, not just the first."""
    data_source: str
    metric_field: str
    aggregation: str
    target_key: Optional[str] = None
    condition: str
    threshold_value: float
    value: Optional[float] = None   # None = no data / held (degraded read)
    breached: bool = False


class AlertTestResult(BaseModel):
    rule_id: str
    current_metric_value: float
    threshold_breached: bool
    query_took_ms: int
    # Test is a dry-run and never fires. These explain whether a *sustained* breach
    # would actually reach a channel, and if not, why — the three gates Test skips.
    would_notify: bool = True
    action_note: str = ""
    # Composite: every clause evaluated (all metrics), combined with AND/OR.
    kind: str = "single"
    notify_when: Optional[str] = None
    clause_results: list[AlertTestClauseResult] = Field(default_factory=list)
    # The engine's actual last read — resolves "Test says breached but engine says OK":
    # they are two different reads (Test skips degradation handling, the engine holds on it).
    engine_state: Optional[str] = None
    engine_last_value: Optional[float] = None
    engine_last_evaluated_at: Optional[datetime] = None
    engine_read_degraded: bool = False


class AlertLogRead(BaseModel):
    id: str
    rule_id: Optional[str] = None  # NULL once the rule is deleted (history survives)
    rule_name: str
    severity: str
    metric_value_at_firing: float
    notified_channels: list[str]
    fired_at: datetime
    resolved_at: Optional[datetime] = None
    event_code: Optional[str] = None

    model_config = {"from_attributes": True}


class AlertLogDetail(AlertLogRead):
    """Full history row for the detail drawer — heavy JSONB included."""
    rule_snapshot: dict = {}
    sent_payloads: dict = {}
