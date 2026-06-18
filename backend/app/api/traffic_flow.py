"""
Traffic Flow API routes (FortiGate AppID flow).
Prefix: /api/v1/traffic-flow

Endpoints:
  GET /summary  — All 9 widgets in a single call
  GET /chart    — 60s stacked bar chart data
  GET /table    — Paginated flow records table
  GET /sankey   — Sankey diagram nodes+links
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import traffic_flow as tf_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_flow import (
    TrafficSummaryResponse,
    TrafficChartResponse,
    TrafficTableResponse,
    SankeyResponse,
)

router = APIRouter(prefix="/api/v1/traffic-flow", tags=["Traffic Flow"])


# ─────────────────────────────────────────────────────────────────
# GET /summary
# ─────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=APIResponse[TrafficSummaryResponse])
async def traffic_flow_summary(
    site_name: str = Query(..., description="Site name (e.g. Site_FGT-DC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter (internet, intra-lan, inter-site, or empty for all)"),
    current_user=Depends(get_current_user),
):
    """Returns all 9 traffic flow widget data for the given site and time range."""
    t0 = time.monotonic()
    data = await tf_qb.flow_summary(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficSummaryResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /chart
# ─────────────────────────────────────────────────────────────────


@router.get("/chart", response_model=APIResponse[TrafficChartResponse])
async def traffic_flow_chart(
    site_name: str = Query(..., description="Site name (e.g. Site_FGT-DC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    bucket_seconds: int = Query(60, description="Bucket interval in seconds (default 60)"),
    current_user=Depends(get_current_user),
):
    """Returns stacked bar chart for application throughput with dynamic bucket interval."""
    t0 = time.monotonic()
    data = await tf_qb.flow_chart(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter, bucket_seconds=bucket_seconds
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficChartResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /table
# ─────────────────────────────────────────────────────────────────


@router.get("/table", response_model=APIResponse[TrafficTableResponse])
async def traffic_flow_table(
    site_name: str = Query(..., description="Site name (e.g. Site_FGT-DC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    after: Optional[str] = Query(None, description="Pagination after_key (JSON string)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    current_user=Depends(get_current_user),
):
    """Returns paginated flow records with composite aggregation."""
    import json

    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try:
            after_key = json.loads(after)
        except json.JSONDecodeError:
            return APIResponse.fail("INVALID_AFTER", "after parameter must be valid JSON")

    data = await tf_qb.flow_table(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key, path_filter=path_filter
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficTableResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /sankey
# ─────────────────────────────────────────────────────────────────


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_flow_sankey(
    site_name: str = Query(..., description="Site name (e.g. Site_FGT-DC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    direction: str = Query("", description="Flow direction: upload or download"),
    current_user=Depends(get_current_user),
):
    """Returns Sankey diagram nodes+links. direction='' for unfiltered, 'upload' or 'download' for zone-based direction."""
    t0 = time.monotonic()
    data = await tf_qb.sankey_data(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter, direction=direction
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=SankeyResponse(**data), meta={"query_took_ms": elapsed})
