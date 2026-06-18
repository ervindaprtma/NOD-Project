"""
Traffic Flow API routes (FortiGate AppID flow).
Prefix: /api/v1/traffic-flow
"""
from __future__ import annotations

import time
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import traffic_flow as tf_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_flow import (
    TrafficSummaryResponse, TrafficChartResponse,
    TrafficTableResponse, SankeyResponse,
)

router = APIRouter(prefix="/api/v1/traffic-flow", tags=["Traffic Flow"])


@router.get("/summary", response_model=APIResponse[TrafficSummaryResponse])
async def traffic_flow_summary(
    site_name: str = Query(..., description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    app_filter: str = Query("", description="Filter: application name"),
    category_filter: str = Query("", description="Filter: application category"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    data = await tf_qb.flow_summary(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficSummaryResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/chart", response_model=APIResponse[TrafficChartResponse])
async def traffic_flow_chart(
    site_name: str = Query(..., description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    bucket_seconds: int = Query(60, description="Bucket interval in seconds"),
    app_filter: str = Query("", description="Filter: application name"),
    category_filter: str = Query("", description="Filter: application category"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    data = await tf_qb.flow_chart(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        bucket_seconds=bucket_seconds,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficChartResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/table", response_model=APIResponse[TrafficTableResponse])
async def traffic_flow_table(
    site_name: str = Query(..., description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    after: Optional[str] = Query(None, description="Pagination after_key"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    app_filter: str = Query("", description="Filter: application name"),
    category_filter: str = Query("", description="Filter: application category"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try: after_key = json.loads(after)
        except json.JSONDecodeError: return APIResponse.fail("INVALID_AFTER", "after must be valid JSON")

    data = await tf_qb.flow_table(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key, path_filter=path_filter,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=TrafficTableResponse(**data), meta={"query_took_ms": elapsed})


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_flow_sankey(
    site_name: str = Query(..., description="Site name"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    path_filter: str = Query("internet", description="Traffic path filter"),
    direction: str = Query("", description="Flow direction: upload or download"),
    app_filter: str = Query("", description="Filter: application name"),
    category_filter: str = Query("", description="Filter: application category"),
    client_ip: str = Query("", description="Filter: client IP"),
    server_ip: str = Query("", description="Filter: server IP"),
    protocol: str = Query("", description="Filter: protocol"),
    dst_port: Optional[int] = Query(None, description="Filter: destination port"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    data = await tf_qb.sankey_data(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        direction=direction,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=SankeyResponse(**data), meta={"query_took_ms": elapsed})
