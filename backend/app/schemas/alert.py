"""
Alert rule and alert log schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

_DATA_SOURCE_RE = r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec|interface_stats|device_uptime)$"


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
    notify_channels: list[str] = Field(default_factory=list)
    notification_template_id: Optional[str] = None
    template_id: Optional[str] = None
    site_name: Optional[str] = Field(default=None, max_length=128)
    target_key: Optional[str] = Field(default=None, max_length=64)  # interface_stats ifIndex
    link_max_mbps: Optional[float] = Field(default=None, ge=0)  # interface_stats %-of-max mode
    kind: str = Field(default="single", pattern=r"^(single|composite)$")
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
    notify_channels: Optional[list[str]] = None
    notification_template_id: Optional[str] = None
    template_id: Optional[str] = None
    site_name: Optional[str] = Field(default=None, max_length=128)
    target_key: Optional[str] = Field(default=None, max_length=64)  # interface_stats ifIndex
    link_max_mbps: Optional[float] = Field(default=None, ge=0)  # interface_stats %-of-max mode
    kind: Optional[str] = Field(default=None, pattern=r"^(single|composite)$")
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
    kind: str = "single"
    notify_when: str = "any"
    clauses: list[dict] = Field(default_factory=list)
    aggregation: str
    condition: str
    threshold_value: float
    evaluation_window_minutes: int
    sustained_for_minutes: int
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


class AlertTestResult(BaseModel):
    rule_id: str
    current_metric_value: float
    threshold_breached: bool
    query_took_ms: int


class AlertLogRead(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    severity: str
    metric_value_at_firing: float
    notified_channels: list[str]
    fired_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
