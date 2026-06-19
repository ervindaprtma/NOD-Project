"""
Report Scheduler Service (P8).
Checks for due scheduled reports every 60 seconds.
Generates reports and distributes them automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.db.models import ReportSchedule, ReportJob

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

CHECK_INTERVAL_SECONDS = 60


async def _check_and_run_schedules():
    """Check for due schedules and trigger report generation."""
    try:
        async with AsyncSessionLocal() as session:
            now = datetime.now(timezone.utc)
            result = await session.execute(
                select(ReportSchedule).where(
                    ReportSchedule.enabled == True,
                    (ReportSchedule.next_run_at <= now) | (ReportSchedule.next_run_at == None),
                )
            )
            schedules = result.scalars().all()

            for schedule in schedules:
                try:
                    await _run_schedule(session, schedule, now)
                except Exception as e:
                    logger.error(f"Failed to run schedule {schedule.id}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Report scheduler check failed: {e}", exc_info=True)


async def _run_schedule(session, schedule: ReportSchedule, now: datetime):
    """Execute a single scheduled report."""
    from app.services.report_generator import generate_report
    from app.services.report_generator import _distribute_report_background

    logger.info(f"Running scheduled report: {schedule.id} type={schedule.report_type}")

    # Calculate time range (last 24 hours by default)
    gte = now - timedelta(hours=24)
    lte = now

    # Create report job
    job = ReportJob(
        report_type=schedule.report_type,
        output_format=schedule.output_format,
        status="pending",
        created_by=schedule.created_by,
        time_range_start=gte,
        time_range_end=lte,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    try:
        job.status = "running"
        await session.commit()

        output_path = await generate_report(job)

        job.status = "completed"
        job.file_path = str(output_path)
        from pathlib import Path
        job.file_size_bytes = Path(output_path).stat().st_size
        job.completed_at = datetime.now(timezone.utc)
        job.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        await session.commit()

        # Auto-distribute if channels configured
        channels = json.loads(schedule.channels) if schedule.channels else []
        if channels:
            asyncio.create_task(
                _distribute_report_background(
                    str(job.id),
                    job.file_path,
                    job.output_format,
                    channels,
                    schedule.recipient_email,
                    schedule.recipient_phone,
                )
            )

        logger.info(f"Scheduled report {job.id} completed and distributed to {channels}")

    except Exception as e:
        job.status = "failed"
        job.error_message = str(e)
        await session.commit()
        logger.error(f"Scheduled report {job.id} failed: {e}")
        raise

    # Update schedule timing
    schedule.last_run_at = now
    schedule.next_run_at = _calculate_next_run(schedule.cron_expression, now)
    await session.commit()


def _calculate_next_run(cron_expr: str, now: datetime) -> datetime:
    """Simple next-run calculator for common cron patterns."""
    parts = cron_expr.strip().split()
    if len(parts) < 5:
        return now + timedelta(hours=24)  # Default: daily

    minute, hour, day, month, weekday = parts

    if hour != "*" and minute != "*":
        # Daily at specific time
        h, m = int(hour), int(minute)
        next_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run

    if hour == "*":
        # Every N minutes
        if minute.startswith("*/"):
            interval = int(minute[2:])
            return now + timedelta(minutes=interval)
        return now + timedelta(hours=1)

    return now + timedelta(hours=24)


def start_report_scheduler():
    """Start the report schedule checker."""
    scheduler.add_job(
        _check_and_run_schedules,
        "interval",
        seconds=CHECK_INTERVAL_SECONDS,
        id="report_schedule_checker",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Report scheduler started (interval={CHECK_INTERVAL_SECONDS}s)")
