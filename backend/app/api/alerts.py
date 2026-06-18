"""
Alert Rules Management API (FR-08, FR-09).
CRUD for alert rules, test rule endpoint, alert logs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user, require_role
from app.db.models import AlertLog, AlertRule, AlertState
from app.db.session import get_db
from app.services.activity_logger import log_activity
from app.schemas.alert import (
    AlertLogRead,
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
    AlertTestResult,
)
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])


# ── Alert Rules CRUD ────────────────────────────────────────────


@router.get("/rules", response_model=APIResponse[list[AlertRuleRead]])
async def list_alert_rules(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """List all alert rules."""
    result = await db.execute(select(AlertRule).order_by(AlertRule.created_at.desc()))
    rules = result.scalars().all()
    return APIResponse.ok(data=[AlertRuleRead.model_validate(r) for r in rules])


@router.post("/rules", response_model=APIResponse[AlertRuleRead], status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """Create a new alert rule."""
    rule = AlertRule(
        name=body.name,
        severity=body.severity,
        data_source=body.data_source,
        metric_field=body.metric_field,
        aggregation=body.aggregation,
        condition=body.condition,
        threshold_value=body.threshold_value,
        evaluation_window_minutes=body.evaluation_window_minutes,
        sustained_for_minutes=body.sustained_for_minutes,
        notify_channels=body.notify_channels,
        template_id=body.template_id,
        enabled=body.enabled,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)

    # Create initial alert state
    db.add(AlertState(rule_id=rule.id, state="INACTIVE"))
    await db.flush()

    return APIResponse.ok(data=AlertRuleRead.model_validate(rule))

    # Fire-and-forget activity log
    import asyncio
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="alert_rule_created",
        details={"rule_name": rule.name, "rule_id": rule.id, "severity": rule.severity},
    ))


@router.get("/rules/{rule_id}", response_model=APIResponse[AlertRuleRead])
async def get_alert_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found.")
    return APIResponse.ok(data=AlertRuleRead.model_validate(rule))


@router.put("/rules/{rule_id}", response_model=APIResponse[AlertRuleRead])
async def update_alert_rule(
    rule_id: str,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found.")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rule, key, value)
    await db.flush()
    await db.refresh(rule)

    import asyncio
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="alert_rule_updated",
        details={"rule_name": rule.name, "rule_id": rule.id, "changes": update_data},
    ))

    return APIResponse.ok(data=AlertRuleRead.model_validate(rule))


@router.delete("/rules/{rule_id}", response_model=APIResponse[dict])
async def delete_alert_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found.")
    await db.delete(rule)
    await db.flush()

    import asyncio
    asyncio.ensure_future(log_activity(
        user_id=current_user.id,
        action="alert_rule_deleted",
        details={"rule_name": rule.name, "rule_id": rule_id},
    ))

    return APIResponse.ok(data={"deleted": rule_id})


# ── Test Rule ───────────────────────────────────────────────────


@router.post("/rules/{rule_id}/test", response_model=APIResponse[AlertTestResult])
async def test_alert_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
):
    """
    FR-09: Test rule — executes the rule's query against live data.
    Returns current metric value. Does NOT fire notification.
    Does NOT alter alert state.
    """
    import time as _time

    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found.")

    t0 = _time.monotonic()

    # Execute the rule's query against OpenSearch
    metric_value = 0.0
    try:
        from app.opensearch import appid as appid_qb
        from app.opensearch import ha as ha_qb
        # Simplified test: map data_source to appropriate query
        # In production, a more sophisticated dispatch would be used
        if rule.data_source == "ha_resource":
            import time as _t
            now_ms = int(_t.time() * 1000)
            window_ms = rule.evaluation_window_minutes * 60 * 1000
            devices = await ha_qb.current_device_status(
                gte_ms=now_ms - window_ms, lte_ms=now_ms
            )
            if devices:
                metric_value = float(devices[0].get(rule.metric_field.split(".")[-1], 0) or 0)
        elif rule.data_source == "appid_flow":
            import time as _t
            now_ms = int(_t.time() * 1000)
            window_ms = rule.evaluation_window_minutes * 60 * 1000
            total = await appid_qb.total_throughput(
                gte_ms=now_ms - window_ms, lte_ms=now_ms
            )
            metric_value = float(total)
    except Exception as e:
        return APIResponse.fail(
            code="QUERY_ERROR",
            message=f"Failed to evaluate rule: {str(e)}",
        )

    elapsed = int((_time.monotonic() - t0) * 1000)

    # Check threshold
    breached = False
    op = rule.condition
    th = rule.threshold_value
    if op == ">":
        breached = metric_value > th
    elif op == "<":
        breached = metric_value < th
    elif op == ">=":
        breached = metric_value >= th
    elif op == "<=":
        breached = metric_value <= th
    elif op == "==":
        breached = abs(metric_value - th) < 0.001

    return APIResponse.ok(
        data=AlertTestResult(
            rule_id=rule_id,
            current_metric_value=metric_value,
            threshold_breached=breached,
            query_took_ms=elapsed,
        )
    )


# ── Alert Logs ──────────────────────────────────────────────────


@router.get("/logs", response_model=APIResponse[list[AlertLogRead]])
async def get_alert_logs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role("admin")),
    limit: int = 50,
    offset: int = 0,
):
    """FR-11: Alert firing history."""
    result = await db.execute(
        select(AlertLog)
        .order_by(AlertLog.fired_at.desc())
        .offset(offset)
        .limit(limit)
    )
    logs = result.scalars().all()
    total = (await db.execute(select(func.count(AlertLog.id)))).scalar() or 0
    return APIResponse.ok(
        data=[AlertLogRead.model_validate(l) for l in logs],
        meta={"total": total},
    )
