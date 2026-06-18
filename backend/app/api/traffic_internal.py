"""
Traffic Internal API routes (intra-lan + inter-site traffic).
Prefix: /api/v1/traffic-internal

Endpoints:
  GET /summary  — 9 widgets (port/service-based)
  GET /chart    — 60s stacked bar chart
  GET /table    — Paginated flow records
  GET /sankey   — Sankey diagram (Zone → Service → Egress → AS Org)
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import traffic_internal as ti_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_inbound import (
    TrafficInboundSummaryResponse,
    TrafficInboundChartResponse,
    TrafficInboundTableResponse,
    SankeyResponse,
)

router = APIRouter(prefix="/api/v1/traffic-internal", tags=["Traffic Internal"])

ALL_SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]


# ─────────────────────────────────────────────────────────────────
# GET /summary
# ─────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=APIResponse[TrafficInboundSummaryResponse])
async def traffic_internal_summary(
    site_name: str = Query("Site_FGT_Office", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """Returns all 9 traffic internal widget data (service/port-based, intra-lan + inter-site)."""
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.flow_summary(gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name)
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundSummaryResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /chart
# ─────────────────────────────────────────────────────────────────


@router.get("/chart", response_model=APIResponse[TrafficInboundChartResponse])
async def traffic_internal_chart(
    site_name: str = Query("Site_FGT_Office", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    bucket_seconds: int = Query(60, description="Bucket interval in seconds (default 60)"),
    current_user=Depends(get_current_user),
):
    """Stacked bar chart for internal service throughput."""
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.flow_chart(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, bucket_seconds=bucket_seconds
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundChartResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /table
# ─────────────────────────────────────────────────────────────────


@router.get("/table", response_model=APIResponse[TrafficInboundTableResponse])
async def traffic_internal_table(
    site_name: str = Query("Site_FGT_Office", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    after: Optional[str] = Query(None, description="Pagination after_key (JSON string)"),
    current_user=Depends(get_current_user),
):
    """Paginated internal flow records with service dimension."""
    import json

    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")

    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try:
            after_key = json.loads(after)
        except json.JSONDecodeError:
            return APIResponse.fail("INVALID_AFTER", "after parameter must be valid JSON")

    data = await ti_qb.flow_table(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundTableResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /sankey
# ─────────────────────────────────────────────────────────────────


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_internal_sankey(
    site_name: str = Query("Site_FGT_Office", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """Sankey: Zone → Service → Egress → AS Org for internal traffic."""
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.sankey_data(gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name)
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=SankeyResponse(**data), meta={"query_took_ms": elapsed})
