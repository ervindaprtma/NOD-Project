"""
Traffic Internal API routes (intra-lan + inter-site traffic).
Prefix: /api/v1/traffic-internal
"""
from __future__ import annotations

import time
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import traffic_internal as ti_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_internal import (
    TrafficInternalSummaryResponse,
    TrafficInternalChartResponse,
    TrafficInternalTableResponse,
    SankeyResponse,
)

router = APIRouter(prefix="/api/v1/traffic-internal", tags=["Traffic Internal"])

ALL_SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]


@router.get("/summary", response_model=APIResponse[TrafficInternalSummaryResponse])
async def traffic_internal_summary(
    site_name: str = Query("Site_FGT-DC", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    app_filter: str = Query("", description="Filter: application name"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data = await ti_qb.flow_summary(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInternalSummaryResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/chart", response_model=APIResponse[TrafficInternalChartResponse])
async def traffic_internal_chart(
    site_name: str = Query("Site_FGT-DC", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    bucket_seconds: int = Query(60, description="Bucket interval in seconds (default 60)"),
    app_filter: str = Query("", description="Filter: application name"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data = await ti_qb.flow_chart(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, bucket_seconds=bucket_seconds,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInternalChartResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/table", response_model=APIResponse[TrafficInternalTableResponse])
async def traffic_internal_table(
    site_name: str = Query("Site_FGT-DC", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    after: Optional[str] = Query(None, description="Pagination after_key (JSON string)"),
    app_filter: str = Query("", description="Filter: application name"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try: after_key = json.loads(after)
        except json.JSONDecodeError: return APIResponse.fail("INVALID_AFTER", "after must be valid JSON")
    data = await ti_qb.flow_table(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficInternalTableResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_internal_sankey(
    site_name: str = Query("Site_FGT-DC", description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    app_filter: str = Query("", description="Filter: application name"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data = await ti_qb.sankey_data(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=SankeyResponse(**data), meta={"query_took_ms": elapsed})
