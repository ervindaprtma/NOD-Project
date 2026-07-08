# PURPOSE: Pydantic schemas for alert_field_catalog API (§11.2)
# SECURITY NOTES: Read-only for viewers; no PII risk

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AlertFieldCatalogRead(BaseModel):
    id: str
    data_source: str
    field_key: str
    display_name: str
    description: str
    unit: str
    category: str  # state | traffic
    valid_aggregations: list[str]
    valid_conditions: list[str]
    example_threshold: Optional[float] = None

    model_config = {"from_attributes": True}