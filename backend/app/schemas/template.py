"""Alert template schemas (v3 §3.12).

Template gallery, from-template creation, and admin template CRUD.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlertTemplateRead(BaseModel):
    """Response shape for a single template in the gallery."""
    id: str
    name: str
    category: str
    icon: str
    description: str
    body_template: str
    underlying_kind: str
    locked_fields: dict[str, Any]
    exposed_fields: list[str]
    is_default: bool
    sort_order: int
    created_at: datetime | None

    model_config = {"from_attributes": True}


class AlertTemplateGallery(BaseModel):
    """Paginated template gallery response."""
    templates: list[AlertTemplateRead]
    total: int


class AlertFromTemplateRequest(BaseModel):
    """User input when creating a rule from a template.

    The template's exposed_fields tells the UI which fields to show.
    The user only provides values for those exposed fields + a rule name.
    """
    name: str = Field(..., min_length=2, max_length=255)
    site_name: str | None = None
    threshold_value: float | None = None
    sustained_minutes: int | None = None
    notify_channels: list[str] | None = None


class AlertFromTemplatePreview(BaseModel):
    """Preview what rule fields the template will produce + test against history."""
    name: str
    data_source: str
    metric_field: str
    aggregation: str
    condition: str
    threshold_value: float
    evaluation_window_minutes: int
    sustained_for_minutes: int
    severity: str
