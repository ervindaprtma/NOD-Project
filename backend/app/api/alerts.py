"""
Alert Rules Management API (FR-08, FR-09).
CRUD for alert rules, test rule endpoint, alert logs.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_role
from app.db.models import AlertFieldCatalog, AlertLog, AlertRule, AlertState, AlertTemplate, User
from app.db.session import get_db
from app.services.activity_logger import log_activity
from app.schemas.alert import (
    AlertLogRead,
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
    AlertTestClauseResult,
    AlertTestResult,
)
from app.schemas.common import APIResponse, Meta
from app.schemas.field_catalog import AlertFieldCatalogRead
from app.schemas.template import AlertFromTemplateRequest, AlertTemplateRead

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])


# ── SSE Stream (v3 §3.10) ──────────────────────────────────────


from fastapi.responses import StreamingResponse
from app.services.sse import sse_event_stream
from app.core.security import create_access_token, decode_token_optional


@router.post("/stream-token")
async def create_stream_token(
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[dict]:
    """Issue a short-lived token for SSE EventSource auth.

    EventSource cannot set custom headers, so the frontend calls this
    endpoint (authenticated via Bearer JWT) to get a ?token= parameter
    for the SSE URL.
    """
    token = create_access_token(
        subject=current_user.id,
        extra_claims={"type": "stream"},
        expires_delta=timedelta(minutes=5),
    )
    return APIResponse.ok(data={"token": token, "expires_in_seconds": 300})


@router.get("/stream")
async def alert_stream(
    request: Request,
    token: str | None = None,
) -> StreamingResponse:
    """Server-Sent Events endpoint for real-time alert delivery.

    Auth: pass a stream token as ?token= query parameter.
    The token is obtained via POST /api/v1/alerts/stream-token.

    Returns text/event-stream with alert, resolved, and heartbeat events.
    """
    # Validate token
    if not token:
        raise HTTPException(status_code=401, detail="Missing stream token. Use POST /api/v1/alerts/stream-token first.")

    payload = decode_token_optional(token)
    if not payload or payload.get("type") != "stream":
        raise HTTPException(status_code=401, detail="Invalid or expired stream token.")

    client_id = payload["sub"]

    return StreamingResponse(
        sse_event_stream(request, client_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Template Gallery (v3 §3.12) ─────────────────────────────────


@router.get("/templates", response_model=APIResponse[list[AlertTemplateRead]])
async def list_templates(
    category: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[list[AlertTemplateRead]]:
    """List all alert templates with optional category and search filters."""
    query = select(AlertTemplate).order_by(AlertTemplate.sort_order, AlertTemplate.name)
    if category:
        query = query.where(AlertTemplate.category == category)
    if search:
        query = query.where(AlertTemplate.name.ilike(f"%{search}%"))
    result = await db.execute(query)
    templates = result.scalars().all()
    return APIResponse.ok(data=[AlertTemplateRead.model_validate(t) for t in templates])


@router.get("/templates/{template_id}", response_model=APIResponse[AlertTemplateRead])
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[AlertTemplateRead]:
    """Get one template with full detail (including locked_fields)."""
    template = await db.get(AlertTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return APIResponse.ok(data=AlertTemplateRead.model_validate(template))


@router.post("/templates/{template_id}/preview", response_model=APIResponse[AlertTemplateRead])
async def preview_template_rule(
    template_id: str,
    body: AlertFromTemplateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("operator")),
) -> APIResponse[AlertTemplateRead]:
    """Preview what a rule created from this template would look like.

    §9.5: actually render subject_template and body_template via sandboxed
    Jinja2 against sample data, so the caller sees the real output, not just
    the raw template text.
    """
    template = await db.get(AlertTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    locked = template.locked_fields

    # Combine locked fields with user input
    merged = dict(locked)
    if body.threshold_value is not None and "threshold_value" in template.exposed_fields:
        merged["threshold_value"] = body.threshold_value
    if body.sustained_minutes is not None and "sustained_minutes" in template.exposed_fields:
        merged["sustained_for_minutes"] = body.sustained_minutes
    if body.site_name and "site_name" in template.exposed_fields:
        merged["site_name"] = body.site_name

    # §9.5: render via sandbox. StrictUndefined on the engine surfaces
    # template typos to the operator at preview time, not at fire time.
    from app.services.alert_engine import _render_template, sample_render_ctx

    # Shared builder → parity with the fire-time ctx (see sample_render_ctx).
    render_ctx = sample_render_ctx(
        name=body.name,
        severity=merged.get("severity", "WARNING"),
        site_name=merged.get("site_name") or "Site_FGT-DC",
        metric_field=merged.get("metric_field", "") or "iface.throughput_mbps",
        condition=merged.get("condition", ">"),
        threshold_value=merged.get("threshold_value", 0.0) or 0.0,
        metric_value=0.0,
        data_source=merged.get("data_source", "") or "interface_stats",
        aggregation=merged.get("aggregation", "avg"),
        fired_at="—",
    )
    rendered = AlertTemplateRead.model_validate(template)
    try:
        if template.subject_template:
            rendered.rendered_subject = _render_template(template.subject_template, render_ctx)
        if template.body_template:
            rendered.rendered_body = _render_template(template.body_template, render_ctx)
    except Exception as e:
        # ponytail: template errors are loud, not silent — surface to the UI
        raise HTTPException(status_code=422, detail=f"Template render failed: {e}")

    return APIResponse.ok(data=rendered)


@router.post("/rules/from-template/{template_id}")
async def create_rule_from_template(
    template_id: str,
    body: AlertFromTemplateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("operator")),
) -> APIResponse[dict]:
    """Create a new alert rule from a template.

    The template's locked_fields are pre-filled and cannot be overridden.
    The user provides only the exposed fields (name, threshold_value, site_name, etc.).
    """
    template = await db.get(AlertTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    locked = template.locked_fields

    # Build the rule from template locked_fields + user exposed_fields
    rule_data = dict(locked)

    # Apply user-provided values for exposed fields
    if body.threshold_value is not None and "threshold_value" in template.exposed_fields:
        rule_data["threshold_value"] = body.threshold_value
    if body.sustained_minutes is not None and "sustained_minutes" in template.exposed_fields:
        rule_data["sustained_for_minutes"] = body.sustained_minutes
    if body.site_name and "site_name" in template.exposed_fields:
        rule_data["site_name"] = body.site_name

    # Ensure notified_channels
    notify_channels = body.notify_channels or []
    if "notify_channels" in template.exposed_fields:
        rule_data["notify_channels"] = notify_channels

    # Build the AlertRule
    kind = template.underlying_kind or "single"
    operator_lookup = {"AND": "all", "OR": "any"}
    notify_when = operator_lookup.get(rule_data.get("operator", ""), "any")

    new_rule = AlertRule(
        name=body.name,
        kind=kind,
        notify_when=notify_when,
        clauses=rule_data.get("clauses", []),
        template_id=template_id,
        severity=rule_data.get("severity", "WARNING"),
        data_source=rule_data.get("data_source", ""),
        metric_field=rule_data.get("metric_field", ""),
        aggregation=rule_data.get("aggregation", "avg"),
        condition=rule_data.get("condition", ">"),
        threshold_value=rule_data.get("threshold_value", 0.0),
        evaluation_window_minutes=rule_data.get("evaluation_window_minutes", 5),
        sustained_for_minutes=rule_data.get("sustained_for_minutes", 3),
        notify_channels=notify_channels,
        site_name=rule_data.get("site_name"),
        enabled=True,
        created_by=current_user.id,
    )
    db.add(new_rule)
    await db.commit()
    await db.refresh(new_rule)

    return APIResponse.ok(
        data={"rule_id": new_rule.id, "rule_name": new_rule.name},
    )


# ── Alert Rules CRUD ────────────────────────────────────────────


@router.get("/rules", response_model=APIResponse[list[AlertRuleRead]])
async def list_alert_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[list[AlertRuleRead]]:
    """List all alert rules, each annotated with its live state + evaluation observability."""
    result = await db.execute(select(AlertRule).order_by(AlertRule.created_at.desc()))
    rules = result.scalars().all()
    state_rows = await db.execute(select(AlertState))
    state_map = {s.rule_id: s for s in state_rows.scalars().all()}

    data = []
    for r in rules:
        read = AlertRuleRead.model_validate(r)
        st = state_map.get(r.id)
        if st is not None:
            read.state = st.state
            read.last_evaluated_at = st.last_evaluated_at
            read.last_value = st.last_value
            read.last_state_change_at = st.last_state_change_at
            read.last_read_degraded = st.last_read_degraded
        data.append(read)
    return APIResponse.ok(data=data)


@router.get("/engine-health")
async def engine_health(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[dict]:
    """Scheduler health for the status line above the rules table (Phase C).

    Returns last-run timing, next-run, and the count of enabled rules. If the last
    completed run is older than 2× the poll interval, the loop has stalled — flagged
    via `stalled` so the UI can warn.
    """
    from app.services.alert_engine import get_engine_health

    health = get_engine_health()
    count_result = await db.execute(
        select(func.count()).select_from(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
    )
    health["enabled_rule_count"] = count_result.scalar_one()

    # Stall self-watch: last run older than 2× interval (only meaningful once it's run).
    stalled = False
    if health["last_run_at"]:
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(health["last_run_at"])).total_seconds()
        stalled = age > 2 * health["interval_seconds"]
    health["stalled"] = stalled

    return APIResponse.ok(data=health)


@router.post("/rules", response_model=APIResponse[AlertRuleRead], status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[AlertRuleRead]:
    """Create a new alert rule."""
    rule = AlertRule(
        name=body.name,
        severity=body.severity,
        data_source=body.data_source,
        metric_field=body.metric_field,
        target_key=body.target_key,
        link_max_mbps=body.link_max_mbps,
        kind=body.kind,
        notify_when=body.notify_when,
        # appid_flow scoping (app/protocol/port) → plain dict, drop empty fields; None if unused.
        appid_filter=(body.appid_filter.model_dump(exclude_none=True) or None) if body.appid_filter else None,
        # Composite clauses are stored as plain dicts (JSONB); the engine reads them via .get().
        clauses=[c.model_dump() for c in body.clauses],
        aggregation=body.aggregation,
        condition=body.condition,
        threshold_value=body.threshold_value,
        evaluation_window_minutes=body.evaluation_window_minutes,
        sustained_for_minutes=body.sustained_for_minutes,
        notify_channels=body.notify_channels,
        template_id=body.template_id,
        notification_template_id=body.notification_template_id,
        site_name=body.site_name,
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


@router.get("/rules/{rule_id}", response_model=APIResponse[AlertRuleRead])
async def get_alert_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[AlertRuleRead]:
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
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[AlertRuleRead]:
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found.")

    update_data = body.model_dump(exclude_unset=True)
    # Normalize the appid filter: drop empty fields, and an all-empty filter clears it (None).
    if "appid_filter" in update_data:
        af = update_data["appid_filter"] or {}
        update_data["appid_filter"] = {k: v for k, v in af.items() if v not in (None, "")} or None
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
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[dict]:
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
    current_user: User = Depends(require_role("admin")),
) -> APIResponse[AlertTestResult]:
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

    # The engine's last actual read — surfaced so "Test says breached, engine says OK"
    # stops being a contradiction (they are two different reads; the engine holds on a
    # degraded read that Test evaluates anyway).
    st_row = (await db.execute(
        select(AlertState).where(AlertState.rule_id == rule_id)
    )).scalar_one_or_none()

    from app.services.notifier_helper import load_channel_configs
    channel_cfgs = await load_channel_configs()

    def _finish(metric_value, breached, clause_results=None):
        elapsed = int((_time.monotonic() - t0) * 1000)
        would_notify = True
        notes: list[str] = []
        if not rule.enabled:
            would_notify = False
            notes.append("rule is DISABLED — the engine skips it entirely (enable it to alert)")
        if not rule.notify_channels:
            would_notify = False
            notes.append("no notification channel selected on this rule")
        else:
            missing = [c for c in rule.notify_channels if c not in channel_cfgs]
            if missing:
                would_notify = False
                notes.append(
                    f"channel(s) {', '.join(missing)} not enabled/configured in Settings → Notifications"
                )
        if would_notify and breached:
            notes.append(
                f"would fire after the breach holds for {rule.sustained_for_minutes} min "
                f"(Test is instant; the engine debounces)"
            )
        return APIResponse.ok(data=AlertTestResult(
            rule_id=rule_id,
            current_metric_value=metric_value,
            threshold_breached=breached,
            query_took_ms=elapsed,
            would_notify=would_notify,
            action_note="; ".join(notes),
            kind=rule.kind or "single",
            notify_when=rule.notify_when,
            clause_results=clause_results or [],
            engine_state=(st_row.state if st_row else None),
            engine_last_value=(st_row.last_value if st_row else None),
            engine_last_evaluated_at=(st_row.last_evaluated_at if st_row else None),
            engine_read_degraded=bool(st_row.last_read_degraded) if st_row else False,
        ))

    t0 = _time.monotonic()

    # ── Composite: evaluate EVERY clause with the exact engine functions, then combine
    # with AND/OR. Reuses _run_group_query (so degradation handling matches the engine)
    # + _extract_per_rule_value_flat + _check_condition — if this says breached, the
    # engine will too. A clause that can't read shows value=None (held), which is why
    # the whole rule "never evaluates" (any held clause → the engine holds the rule).
    if (rule.kind or "single") == "composite":
        from app.services.alert_engine import (
            _run_group_query, _extract_per_rule_value_flat, _check_condition,
        )
        try:
            clause_results = []
            breaches = []
            values = []
            for clause in (rule.clauses or []):
                ds = clause.get("data_source")
                mf = clause.get("metric_field")
                if not isinstance(ds, str) or not isinstance(mf, str):
                    continue
                cond = clause.get("condition", ">")
                thresh = float(clause.get("threshold_value", 0.0) or 0.0)
                window = clause.get("evaluation_window_minutes", rule.evaluation_window_minutes)
                tkey = clause.get("target_key")
                agg = clause.get("aggregation", "avg")
                gr = await _run_group_query(ds, rule.site_name, window)
                val = _extract_per_rule_value_flat(ds, mf, gr, tkey, agg) if gr is not None else None
                breached_c = bool(val is not None and _check_condition(val, cond, thresh))
                if val is not None:
                    values.append(val)
                breaches.append(breached_c)
                clause_results.append(AlertTestClauseResult(
                    data_source=ds, metric_field=mf, aggregation=agg, target_key=tkey,
                    condition=cond, threshold_value=thresh, value=val, breached=breached_c,
                ))
            # A clause that couldn't read (value None) means the engine HOLDS the whole rule.
            any_held = any(c.value is None for c in clause_results)
            notify_when = rule.notify_when or "any"
            if any_held:
                combined = False  # engine holds → never fires until every clause reads
            elif notify_when == "all":
                combined = bool(clause_results) and all(breaches)
            else:
                combined = any(breaches)
            reported = max(values) if values else 0.0
            resp = _finish(reported, combined, clause_results)
            if any_held:
                held = [c.metric_field for c in clause_results if c.value is None]
                extra = (f"HELD — clause(s) {', '.join(held)} returned no data this read; "
                         f"the engine holds the whole rule (status stays OK) until every clause reads.")
                resp.data.action_note = (resp.data.action_note + "; " + extra).lstrip("; ")
                resp.data.would_notify = False
            return resp
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to evaluate composite rule: {str(e)}",
            )

    # Execute the rule's query against OpenSearch
    metric_value = 0.0
    try:
        from app.opensearch import ha as ha_qb
        from app.opensearch import sdwan as sdwan_qb
        from app.opensearch import sslvpn as sslvpn_qb
        from app.opensearch import ipsec as ipsec_qb
        from app.opensearch import traffic_flow as tf_qb
        import time as _t
        now_ms = int(_t.time() * 1000)
        window_ms = rule.evaluation_window_minutes * 60 * 1000
        gte_ms = now_ms - window_ms
        lte_ms = now_ms

        if rule.data_source == "ha_resource":
            devices = await ha_qb.current_device_status(
                gte_ms=gte_ms, lte_ms=lte_ms
            )
            if devices and rule.metric_field.startswith("ha_member."):
                field_name = rule.metric_field.split(".", 1)[1]
                metric_value = float(devices[0].get(field_name, 0) or 0)
        elif rule.data_source == "appid_flow":
            # Same per-path summary + extractor the engine uses, so the dry-run
            # value matches what the rule will actually evaluate.
            from app.services.alert_engine import _extract_appid_flow
            af = rule.appid_filter or {}
            flow_summary = await tf_qb.appid_flow_alert_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=rule.site_name or "Site_FGT-DC",
                app_filter=af.get("app") or "", protocol=af.get("protocol") or "",
                dst_port=af.get("port"),
            )
            metric_value = _extract_appid_flow(rule.metric_field, flow_summary) if isinstance(flow_summary, dict) else 0.0
        elif rule.data_source == "sdwan_sla":
            site = rule.site_name or "Site_FGT-DC"
            summary = await sdwan_qb.sla_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
            )
            import re
            m = re.match(r'^(\w+)_link(\d+)$', rule.metric_field)
            base_key = m.group(1) if m else rule.metric_field
            link_idx = (int(m.group(2)) - 1) if m else 0
            vals = summary.get(base_key, [0.0])
            metric_value = float(vals[link_idx] if link_idx < len(vals) else (vals[0] if isinstance(vals, list) else vals or 0.0))
        elif rule.data_source == "vpn_ssl":
            from app.services.alert_engine import _extract_vpn_usage
            site = sslvpn_qb.sslvpn_measurement_for_site(rule.site_name)
            usage = await sslvpn_qb.sslvpn_usage_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
            )
            metric_value = _extract_vpn_usage(rule.metric_field, usage)
        elif rule.data_source == "vpn_ipsec":
            from app.services.alert_engine import _extract_vpn_usage
            usage = await ipsec_qb.ipsec_usage_summary(gte_ms=gte_ms, lte_ms=lte_ms)
            metric_value = _extract_vpn_usage(rule.metric_field, usage)
        elif rule.data_source == "interface_stats":
            # Same summary + extractor the engine uses, so the dry-run matches live.
            from app.opensearch import interface_stats as if_qb
            from app.services.alert_engine import _extract_interface_stats
            iface_summary = await if_qb.interface_stats_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=rule.site_name or "Site_FGT-DC"
            )
            metric_value = _extract_interface_stats(
                rule.metric_field, rule.target_key, rule.aggregation, iface_summary
            ) if isinstance(iface_summary, dict) else 0.0
        elif rule.data_source == "device_uptime":
            # Same summary + extractor the engine uses, so the dry-run matches live.
            from app.opensearch import device_uptime as du_qb
            from app.services.alert_engine import _extract_device_uptime
            avail = await du_qb.device_availability(
                site_name=rule.site_name or "Site_FGT-DC", gte_ms=gte_ms, lte_ms=lte_ms
            )
            extracted = _extract_device_uptime(
                rule.metric_field, rule.target_key, avail
            ) if isinstance(avail, dict) else None
            # None = "unknown" (device absent / insufficient history) — the engine holds;
            # for the dry-run surface it as 0 so the operator sees "not breaching".
            metric_value = extracted if extracted is not None else 0.0
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to evaluate rule: {str(e)}",
        )

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

    return _finish(metric_value, breached)


# ── Alert Logs ──────────────────────────────────────────────────


@router.get("/logs", response_model=APIResponse[list[AlertLogRead]])
async def get_alert_logs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
    limit: int = 50,
    offset: int = 0,
) -> APIResponse[list[AlertLogRead]]:
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
        meta=Meta(total=total),
    )


# ── Field Catalog (§11.2) ───────────────────────────────────────────────


@router.get("/fields", response_model=APIResponse[list[AlertFieldCatalogRead]])
async def get_field_catalog(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("viewer")),
    data_source: str | None = None,
) -> APIResponse[list[AlertFieldCatalogRead]]:
    """FR-11.2: Field catalog for guided rule creation (viewer auth)."""
    stmt = select(AlertFieldCatalog)
    if data_source:
        stmt = stmt.where(AlertFieldCatalog.data_source == data_source)

    result = await db.execute(stmt.order_by(AlertFieldCatalog.field_key))
    fields = result.scalars().all()
    return APIResponse.ok(data=[AlertFieldCatalogRead.model_validate(f) for f in fields])


@router.get("/interfaces")
async def get_site_interfaces(
    site_name: str,
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[list[dict]]:
    """Phase E: per-site interface list for the builder's interface picker (→ target_key).

    Sourced from the hardcoded SITE_IFINDEX_MAP, ordered by SITE_IFACE_SORT_ORDER (WAN
    first, MPLS second) to match the dashboard layout.
    """
    from app.opensearch.interface_stats import SITE_IFINDEX_MAP, SITE_IFACE_SORT_ORDER

    iface_map = SITE_IFINDEX_MAP.get(site_name)
    if not iface_map:
        raise HTTPException(status_code=404, detail=f"No interfaces mapped for site: {site_name}")
    order = SITE_IFACE_SORT_ORDER.get(site_name, {})
    keys = sorted(iface_map.keys(), key=lambda k: order.get(k, 999))
    return APIResponse.ok(data=[{"key": k, "label": iface_map[k]} for k in keys])


@router.get("/devices")
async def get_site_devices(
    site_name: str,
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[list[dict]]:
    """Track AL: per-site device list for the builder's device picker (→ target_key).

    key is the device IP (tag.source, the stable identity the engine matches on);
    label is the hostname. Sourced from live telemetry via device_availability().
    """
    from app.opensearch import device_uptime as du_qb

    avail = await du_qb.device_availability(site_name=site_name, window="24h")
    devices = avail.get("devices", []) if isinstance(avail, dict) else []
    return APIResponse.ok(data=[
        {"key": d["device_key"], "label": d.get("hostname") or d["device_key"]}
        for d in devices
    ])


@router.get("/sdwan-links")
async def get_site_sdwan_links(
    site_name: str,
    current_user: User = Depends(require_role("viewer")),
) -> APIResponse[list[dict]]:
    """Per-site SD-WAN link list for the alert link picker (→ target_key = link number).

    Every link the site has (DC/DRC: 4, Office: 6) with its real name (WAN uplink or
    IPsec/ADVPN tunnel). key is the 1-based link number the engine indexes on.
    """
    from app.schemas.sdwan_resource_vpn import SITE_LINK_COUNT, SITE_LINK_LABELS, SITE_LINK_TYPES

    n = SITE_LINK_COUNT.get(site_name)
    if not n:
        raise HTTPException(status_code=404, detail=f"No SD-WAN links mapped for site: {site_name}")
    labels = SITE_LINK_LABELS.get(site_name, {})
    types = SITE_LINK_TYPES.get(site_name, {})
    return APIResponse.ok(data=[
        {"key": str(i), "label": labels.get(f"link{i}", f"link{i}"), "type": types.get(f"link{i}", "")}
        for i in range(1, n + 1)
    ])
