"""
Traffic Inbound API routes (VIP Inbound traffic analytics).
Prefix: /api/v1/traffic-inbound

Endpoints:
  GET /summary  — All widgets (port/service-based)
  GET /chart    — 60s stacked bar chart data
  GET /table    — Paginated flow records table
  GET /sankey   — Sankey diagram nodes+links
"""
from __future__ import annotations

import time
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import traffic_inbound as ti_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_inbound import (
    TrafficInboundSummaryResponse,
    TrafficInboundChartResponse,
    TrafficInboundTableResponse,
    SankeyResponse,
)

router = APIRouter(prefix="/api/v1/traffic-inbound", tags=["Traffic Inbound"])

# Only DC and DRC for inbound VIP traffic
ALLOWED_SITES = ["Site_FGT-DC", "Site_FGT-DRC"]


# ─────────────────────────────────────────────────────────────────
# GET /summary
# ─────────────────────────────────────────────────────────────────


@router.get("/summary", response_model=APIResponse[TrafficInboundSummaryResponse])
async def traffic_inbound_summary(
    site_name: str = Query("Site_FGT-DC", description="Site name (Site_FGT-DC or Site_FGT-DRC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    app_filter: str = Query("", description="Filter: application name (wildcard match)"),
    client_ip: str = Query("", description="Filter: client IP address"),
    server_ip: str = Query("", description="Filter: server IP address"),
    protocol: str = Query("", description="Filter: protocol name"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port number"),
    current_user=Depends(get_current_user),
):
    """Returns all traffic inbound widget data (service/port-based)."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.flow_summary(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter="inbound-vip",
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundSummaryResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /chart
# ─────────────────────────────────────────────────────────────────


@router.get("/chart", response_model=APIResponse[TrafficInboundChartResponse])
async def traffic_inbound_chart(
    site_name: str = Query("Site_FGT-DC", description="Site name (Site_FGT-DC or Site_FGT-DRC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    bucket_seconds: int = Query(60, description="Bucket interval in seconds (default 60)"),
    app_filter: str = Query("", description="Filter: application name (wildcard match)"),
    client_ip: str = Query("", description="Filter: client IP address"),
    server_ip: str = Query("", description="Filter: server IP address"),
    protocol: str = Query("", description="Filter: protocol name"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port number"),
    current_user=Depends(get_current_user),
):
    """Returns stacked bar chart for service throughput (port-based)."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.flow_chart(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        path_filter="inbound-vip", bucket_seconds=bucket_seconds,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundChartResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /table
# ─────────────────────────────────────────────────────────────────


@router.get("/table", response_model=APIResponse[TrafficInboundTableResponse])
async def traffic_inbound_table(
    site_name: str = Query("Site_FGT-DC", description="Site name (Site_FGT-DC or Site_FGT-DRC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    after: Optional[str] = Query(None, description="Pagination after_key (JSON string)"),
    app_filter: str = Query("", description="Filter: application name (wildcard match)"),
    client_ip: str = Query("", description="Filter: client IP address"),
    server_ip: str = Query("", description="Filter: server IP address"),
    protocol: str = Query("", description="Filter: protocol name"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port number"),
    current_user=Depends(get_current_user),
):
    """Returns paginated inbound flow records with composite aggregation."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")

    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try:
            after_key = json.loads(after)
        except json.JSONDecodeError:
            return APIResponse.fail("INVALID_AFTER", "after parameter must be valid JSON")

    data = await ti_qb.flow_table(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        after=after_key, path_filter="inbound-vip",
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInboundTableResponse(**data), meta={"query_took_ms": elapsed})


# ─────────────────────────────────────────────────────────────────
# GET /sankey
# ─────────────────────────────────────────────────────────────────


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_inbound_sankey(
    site_name: str = Query("Site_FGT-DC", description="Site name (Site_FGT-DC or Site_FGT-DRC)"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    direction: str = Query("", description="Flow direction: upload or download"),
    app_filter: str = Query("", description="Filter: application name (wildcard match)"),
    client_ip: str = Query("", description="Filter: client IP address"),
    server_ip: str = Query("", description="Filter: server IP address"),
    protocol: str = Query("", description="Filter: protocol name"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port number"),
    current_user=Depends(get_current_user),
):
    """Returns Sankey diagram nodes+links. direction='' for unfiltered, 'upload' or 'download' for zone-based direction."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")

    t0 = time.monotonic()
    data = await ti_qb.sankey_data(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter="inbound-vip",
        direction=direction,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=SankeyResponse(**data), meta={"query_took_ms": elapsed})
