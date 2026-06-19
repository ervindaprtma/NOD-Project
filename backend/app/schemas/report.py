"""
Report job schemas.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    report_type: str = Field(..., pattern=r"^(R-01|R-02|R-03|R-04|R-05|R-06|R-07|R-08)$")
    output_format: str = Field(default="pdf", pattern=r"^(pdf|html|docx)$")
    time_range_start: int  # UTC epoch ms
    time_range_end: int  # UTC epoch ms
    sites: Optional[list[str]] = Field(
        default=["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"],
        description="List of sites to include; defaults to all 3 sites",
    )
    sections: Optional[list[str]] = Field(
        default=None,
        description="List of report sections to include; empty/None = all sections",
    )


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
