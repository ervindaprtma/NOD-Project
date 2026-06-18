"""
Alert rule and alert log schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    severity: str = Field(..., pattern=r"^(INFO|WARNING|CRITICAL)$")
    data_source: str = Field(..., pattern=r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec)$")
    metric_field: str = Field(..., min_length=1, max_length=255)
    aggregation: str = Field(..., pattern=r"^(avg|max|min|sum|count)$")
    condition: str = Field(..., pattern=r"^(>|<|>=|<=|==)$")
    threshold_value: float
    evaluation_window_minutes: int = Field(..., ge=1)
    sustained_for_minutes: int = Field(..., ge=0)
    notify_channels: list[str] = Field(default_factory=list)
    template_id: Optional[str] = None
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    severity: Optional[str] = Field(default=None, pattern=r"^(INFO|WARNING|CRITICAL)$")
    data_source: Optional[str] = Field(default=None, pattern=r"^(appid_flow|sdwan_sla|ha_resource|vpn_ssl|vpn_ipsec)$")
    metric_field: Optional[str] = Field(default=None, min_length=1, max_length=255)
    aggregation: Optional[str] = Field(default=None, pattern=r"^(avg|max|min|sum|count)$")
    condition: Optional[str] = Field(default=None, pattern=r"^(>|<|>=|<=|==)$")
    threshold_value: Optional[float] = None
    evaluation_window_minutes: Optional[int] = Field(default=None, ge=1)
    sustained_for_minutes: Optional[int] = Field(default=None, ge=0)
    notify_channels: Optional[list[str]] = None
    template_id: Optional[str] = None
    enabled: Optional[bool] = None


class AlertRuleRead(BaseModel):
    id: str
    name: str
    severity: str
    data_source: str
    metric_field: str
    aggregation: str
    condition: str
    threshold_value: float
    evaluation_window_minutes: int
    sustained_for_minutes: int
    notify_channels: list[str]
    template_id: Optional[str] = None
    enabled: bool
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime

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
