"""
Report API (FR-12, FR-13).
Async report generation via background tasks.
Reports stored temporarily, purged after TTL.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, require_role
from app.db.models import ReportJob
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.report import ReportDistributeRequest, ReportGenerateRequest, ReportJobStatus
from app.services.activity_logger import log_activity

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])

REPORT_OUTPUT_DIR = Path("reports/output")
REPORT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_TTL_HOURS = 1  # Purge after 1 hour


@router.get("", response_model=APIResponse[list[ReportJobStatus]])
async def list_reports(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all report jobs for the current user (operator+ see all)."""
    is_operator = current_user.role in ("operator", "admin", "superadmin")
    query = select(ReportJob).order_by(ReportJob.created_at.desc())
    if not is_operator:
        query = query.where(ReportJob.created_by == current_user.id)
    result = await db.execute(query.limit(50))
    jobs = result.scalars().all()
    return APIResponse.ok(data=[ReportJobStatus.model_validate(j) for j in jobs])


@router.post("/generate", response_model=APIResponse[dict], status_code=status.HTTP_202_ACCEPTED)
async def generate_report(
    body: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """
    FR-12: Trigger async report generation.
    Returns 202 with job_id immediately. Client polls /status/{job_id}.
    """
    time_start = datetime.fromtimestamp(body.time_range_start / 1000, tz=timezone.utc)
    time_end = datetime.fromtimestamp(body.time_range_end / 1000, tz=timezone.utc)

    job = ReportJob(
        report_type=body.report_type,
        output_format=body.output_format,
        status="pending",
        created_by=current_user.id,
        time_range_start=time_start,
        time_range_end=time_end,
        sites=body.sites if body.sites else ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"],
        sections=body.sections,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Schedule background generation
    asyncio.create_task(
        _generate_report_background(str(job.id), body.report_type, body.output_format)
    )

    # Log activity
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="report_generated",
        details={"job_id": str(job.id), "report_type": body.report_type, "format": body.output_format},
    ))

    return APIResponse.ok(
        data={"job_id": str(job.id), "status": "pending"},
        meta={"message": "Report generation started. Poll /reports/status/{job_id} for completion."},
    )


@router.get("/status/{job_id}", response_model=APIResponse[ReportJobStatus])
async def get_report_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Poll report generation status."""
    result = await db.execute(select(ReportJob).where(ReportJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found.")

    return APIResponse.ok(data=ReportJobStatus.model_validate(job))


@router.get("/download/{job_id}")
async def download_report(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """Download a completed report file."""
    result = await db.execute(select(ReportJob).where(ReportJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found.")

    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=400, detail="Report not ready or generation failed.")

    if not os.path.exists(job.file_path):
        raise HTTPException(status_code=404, detail="Report file expired or not found.")

    media_types = {
        "pdf": "application/pdf",
        "html": "text/html",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    media_type = media_types.get(job.output_format, "application/octet-stream")
    filename = f"nod_report_{job_id[:8]}.{job.output_format}"

    # Log download
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="report_downloaded",
        details={"job_id": str(job.id), "report_type": job.report_type, "format": job.output_format, "size_bytes": job.file_size_bytes},
    ))

    return FileResponse(
        path=job.file_path,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/preview/{job_id}")
async def preview_report(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Preview a completed HTML report in the browser."""
    result = await db.execute(select(ReportJob).where(ReportJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found.")

    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=400, detail="Report not ready or generation failed.")

    if not os.path.exists(job.file_path):
        raise HTTPException(status_code=404, detail="Report file expired or not found.")

    if job.output_format != "html":
        raise HTTPException(status_code=400, detail="Preview only available for HTML reports.")

    return FileResponse(
        path=job.file_path,
        media_type="text/html",
    )


@router.post("/distribute/{job_id}", response_model=APIResponse[dict])
async def distribute_report(
    job_id: str,
    body: ReportDistributeRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """FR-13: Distribute report to configured channels."""
    result = await db.execute(select(ReportJob).where(ReportJob.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found.")

    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=400, detail="Report not ready for distribution.")

    # File size guard (20 MB)
    if job.file_size_bytes and job.file_size_bytes > 20 * 1024 * 1024:
        return APIResponse.fail(
            code="FILE_TOO_LARGE",
            message="Report exceeds 20 MB. Cannot distribute automatically.",
        )

    # Schedule background distribution
    asyncio.create_task(
        _distribute_report_background(
            str(job.id),
            job.file_path,
            job.output_format,
            body.channels,
            body.recipient_email,
            body.recipient_phone,
        )
    )

    # Log distribution
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="report_distributed",
        details={"job_id": str(job.id), "channels": body.channels},
    ))

    return APIResponse.ok(data={"job_id": str(job.id), "channels": body.channels})


# ─────────────────────────────────────────────────────────────────
# Background tasks (stubs — full implementation in Phase 5)
# ─────────────────────────────────────────────────────────────────


async def _generate_report_background(job_id: str, report_type: str, output_format: str):
    """
    Background task: generate report using report_generator service.
    """
    import logging
    import traceback
    from app.services.report_generator import generate_report

    logger = logging.getLogger(__name__)
    logger.info(f"BG task started: job={job_id} type={report_type} fmt={output_format}")

    from app.db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(ReportJob).where(ReportJob.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                logger.error(f"BG task: job {job_id} not found")
                return

            job.status = "running"
            await session.commit()

            try:
                output_path = await generate_report(job)

                job.status = "completed"
                job.file_path = str(output_path)
                job.file_size_bytes = Path(output_path).stat().st_size
                job.completed_at = datetime.now(timezone.utc)
                job.expires_at = datetime.now(timezone.utc) + timedelta(hours=REPORT_TTL_HOURS)
                await session.commit()
                logger.info(f"Report {job_id} completed: {output_path}")

            except Exception as e:
                job.status = "failed"
                job.error_message = str(e)
                await session.commit()
                logger.error(f"Report {job_id} failed: {e}")
                logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"BG task outer error for {job_id}: {e}")
        logger.error(traceback.format_exc())


async def _distribute_report_background(
    job_id: str,
    file_path: str,
    output_format: str,
    channels: list[str],
    recipient_email: str | None = None,
    recipient_phone: str | None = None,
):
    """
    Background task: distribute report to all requested channels.
    """
    import logging
    from pathlib import Path
    from app.services.notifiers.email import send_email_with_attachment
    from app.services.notifiers.telegram import send_telegram_document
    from app.services.notifiers.discord import send_discord_file
    from app.services.notifiers.whatsapp import send_whatsapp_document

    logger = logging.getLogger(__name__)
    fname = Path(file_path).name
    caption = f"NOD Report — {job_id[:8]} ({output_format.upper()})"

    results = {}

    if "email" in channels:
        results["email"] = await send_email_with_attachment(
            subject=f"NOD Report {job_id[:8]}",
            body=f"Please find attached the requested report (format: {output_format}).",
            file_path=file_path,
            recipient=recipient_email,
        )

    if "telegram" in channels:
        results["telegram"] = await send_telegram_document(
            file_path=file_path, caption=caption
        )

    if "discord" in channels:
        results["discord"] = await send_discord_file(
            file_path=file_path, message=f"📊 **NOD Report** — Job `{job_id[:8]}`"
        )

    if "whatsapp" in channels:
        results["whatsapp"] = await send_whatsapp_document(
            file_path=file_path,
            caption=caption,
            recipient_phone=recipient_phone,
        )

    logger.info(f"Report {job_id} distribution results: {results}")


# ─────────────────────────────────────────────────────────────────
# Scheduled Reports (P8)
# ─────────────────────────────────────────────────────────────────

import json

from app.db.models import ReportSchedule


@router.get("/schedules", response_model=APIResponse[list[dict]])
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all report schedules for the current user (admin sees all)."""
    is_admin = current_user.role in ("admin", "superadmin")
    query = select(ReportSchedule).order_by(ReportSchedule.created_at.desc())
    if not is_admin:
        query = query.where(ReportSchedule.created_by == current_user.id)
    result = await db.execute(query.limit(50))
    schedules = result.scalars().all()
    return APIResponse.ok(data=[{
        "id": str(s.id),
        "report_type": s.report_type,
        "output_format": s.output_format,
        "cron_expression": s.cron_expression,
        "sites": json.loads(s.sites) if s.sites else [],
        "sections": json.loads(s.sections) if s.sections else [],
        "channels": json.loads(s.channels) if s.channels else [],
        "recipient_email": s.recipient_email,
        "recipient_phone": s.recipient_phone,
        "enabled": s.enabled,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "created_at": s.created_at.isoformat(),
    } for s in schedules])


@router.post("/schedules", response_model=APIResponse[dict], status_code=status.HTTP_201_CREATED)
async def create_schedule(
    report_type: str,
    cron_expression: str,
    output_format: str = "html",
    sites: list[str] | None = None,
    sections: list[str] | None = None,
    channels: list[str] | None = None,
    recipient_email: str | None = None,
    recipient_phone: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """Create a new report schedule."""
    schedule = ReportSchedule(
        report_type=report_type,
        output_format=output_format,
        cron_expression=cron_expression,
        sites=json.dumps(sites) if sites else None,
        sections=json.dumps(sections) if sections else None,
        channels=json.dumps(channels) if channels else None,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        enabled=True,
        created_by=current_user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return APIResponse.ok(data={"id": str(schedule.id), "status": "created"})


@router.patch("/schedules/{schedule_id}", response_model=APIResponse[dict])
async def update_schedule(
    schedule_id: str,
    enabled: bool | None = None,
    cron_expression: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """Update a report schedule (enable/disable, change cron)."""
    result = await db.execute(select(ReportSchedule).where(ReportSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    if enabled is not None:
        schedule.enabled = enabled
    if cron_expression is not None:
        schedule.cron_expression = cron_expression
    await db.commit()
    return APIResponse.ok(data={"id": str(schedule.id), "status": "updated"})


@router.delete("/schedules/{schedule_id}", response_model=APIResponse[dict])
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("operator")),
):
    """Delete a report schedule."""
    result = await db.execute(select(ReportSchedule).where(ReportSchedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found.")

    await db.delete(schedule)
    await db.commit()
    return APIResponse.ok(data={"id": str(schedule_id), "status": "deleted"})
