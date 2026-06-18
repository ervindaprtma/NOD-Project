"""
Notification schemas.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationRead(BaseModel):
    id: str
    alert_name: str
    severity: str
    message: str
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
