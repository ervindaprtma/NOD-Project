"""
Report job schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    report_type: str = Field(..., pattern=r"^(R-01|R-02|R-03|R-04)$")
    output_format: str = Field(default="pdf", pattern=r"^(pdf|html|docx)$")
    time_range_start: int  # UTC epoch ms
    time_range_end: int  # UTC epoch ms


class ReportJobStatus(BaseModel):
    job_id: str = Field(validation_alias="id")
    report_type: str
    output_format: str
    status: str  # pending, running, completed, failed
    file_size_bytes: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ReportDistributeRequest(BaseModel):
    channels: list[str]  # email, telegram, whatsapp, discord
    recipient_email: Optional[str] = None
    recipient_phone: Optional[str] = None
