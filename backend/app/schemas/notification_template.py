# PURPOSE: Pydantic schemas for NotificationTemplate CRUD endpoints (§11.1)
# SECURITY NOTES: Templates are rendered in sandboxed Jinja2; only allow-listed vars

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NotificationTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = ""
    subject_template: Optional[str] = "Alert: {{ rule.name }}"
    body_template: str
    line_template: Optional[str] = ""  # optional, for line-based notifiers


class NotificationTemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    subject_template: Optional[str] = None
    body_template: Optional[str] = None
    line_template: Optional[str] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None


class NotificationTemplateRead(BaseModel):
    id: str
    name: str
    description: str
    subject_template: str
    body_template: str
    line_template: Optional[str] = None
    is_default: bool
    is_active: bool = True
    is_user_created: bool
    used_by_count: Optional[int] = 0  # computed field for UI

    model_config = {"from_attributes": True}


class NotificationTemplatePreview(BaseModel):
    """Preview request with sample context values."""
    name: Optional[str] = "Test Rule"
    severity: Optional[str] = "WARNING"
    site_name: Optional[str] = None
    metric_field: Optional[str] = "cpu.usage"
    condition: Optional[str] = ">"
    threshold_value: Optional[float] = 80.0
    metric_value: Optional[float] = 95.5
    fired_at: Optional[str] = None  # sample timestamp; the engine always passes fired_at