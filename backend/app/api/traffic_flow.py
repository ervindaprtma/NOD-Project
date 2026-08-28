"""
Traffic Flow API routes (FortiGate AppID flow).
Prefix: /api/v1/traffic-flow

All endpoints have try/except error handling to prevent 500 errors on
OpenSearch timeouts/connection issues. Returns empty result on error
so the frontend can show "No data" instead of "Failed to load".
"""
from __future__ import annotations

import time
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from app.api._safe import build_meta, safe_query, pack_excludes
from app.api.auth import get_current_user
from app.opensearch.query import track_degradation
from app.opensearch import traffic_flow as tf_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_flow import (
    TrafficSummaryResponse, TrafficChartResponse,
    TrafficTableResponse, SankeyResponse,
)

logger = logging.getLogger("nod.api.traffic_flow")
router = APIRouter(prefix="/api/v1/traffic-flow", tags=["Traffic Flow"])


@router.get("/summary", response_model=APIResponse[TrafficSummaryResponse])
async def traffic_flow_summary(
    request: Request,
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
    dst_as_org: str = Query("", description="Filter: destination AS org (comma-separated)"),
    ingress_interface: str = Query("", description="Filter: ingress interface"),
    egress_interface: str = Query("", description="Filter: egress interface"),
    risk_filter: str = Query("", description="Filter: application risk"),
    vendor_filter: str = Query("", description="Filter: application vendor"),
    tech_filter: str = Query("", description="Filter: application technology"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        tf_qb.flow_summary,
        exclude=pack_excludes(request),
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org,
        risk_filter=risk_filter, vendor_filter=vendor_filter, tech_filter=tech_filter,
        ingress_interface=ingress_interface, egress_interface=egress_interface,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        # Return empty summary on error
        empty = {
            "total_bytes": 0, "total_upload": 0, "total_download": 0, "total_sessions": 0,
            "top_apps": [], "app_categories": [],
            "top_dst_as_org": [], "top_dst_as_country": [], "top_src_as_org": [],
            "top_clients": [], "top_servers": [],
            "protocol_dist": [], "egress_breakdown": [], "ingress_breakdown": [],
        }
        logger.warning(f"summary empty result for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=TrafficSummaryResponse(**empty), meta=meta)
    return APIResponse.ok(data=TrafficSummaryResponse(**data), meta=meta)


@router.get("/chart", response_model=APIResponse[TrafficChartResponse])
async def traffic_flow_chart(
    request: Request,
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
    dst_as_org: str = Query("", description="Filter: destination AS org (comma-separated)"),
    risk_filter: str = Query("", description="Filter: application risk"),
    vendor_filter: str = Query("", description="Filter: application vendor"),
    tech_filter: str = Query("", description="Filter: application technology"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        tf_qb.flow_chart,
        exclude=pack_excludes(request),
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        bucket_seconds=bucket_seconds,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org,
        risk_filter=risk_filter, vendor_filter=vendor_filter, tech_filter=tech_filter,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        logger.warning(f"chart empty result for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficChartResponse(chart_data=[], app_names=[]),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficChartResponse(**data), meta=meta)


@router.get("/table", response_model=APIResponse[TrafficTableResponse])
async def traffic_flow_table(
    request: Request,
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
    dst_as_org: str = Query("", description="Filter: destination AS org (comma-separated)"),
    risk_filter: str = Query("", description="Filter: application risk"),
    vendor_filter: str = Query("", description="Filter: application vendor"),
    tech_filter: str = Query("", description="Filter: application technology"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    degraded = track_degradation()
    after_key: Optional[dict] = None
    if after:
        try: after_key = json.loads(after)
        except json.JSONDecodeError: return APIResponse.fail("INVALID_AFTER", "after must be valid JSON")
    data, err = await safe_query(
        tf_qb.flow_table,
        exclude=pack_excludes(request),
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key, path_filter=path_filter,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org,
        risk_filter=risk_filter, vendor_filter=vendor_filter, tech_filter=tech_filter,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        logger.warning(f"table empty result for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficTableResponse(records=[], after_key=None),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficTableResponse(**data), meta=meta)


@router.get("/sankey", response_model=APIResponse[SankeyResponse])
async def traffic_flow_sankey(
    request: Request,
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
    dst_as_org: str = Query("", description="Filter: destination AS org (comma-separated)"),
    risk_filter: str = Query("", description="Filter: application risk"),
    vendor_filter: str = Query("", description="Filter: application vendor"),
    tech_filter: str = Query("", description="Filter: application technology"),
    current_user=Depends(get_current_user),
):
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        tf_qb.sankey_data,
        exclude=pack_excludes(request),
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter=path_filter,
        direction=direction,
        app_filter=app_filter, category_filter=category_filter,
        client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org,
        risk_filter=risk_filter, vendor_filter=vendor_filter, tech_filter=tech_filter,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        logger.warning(f"sankey empty result for {site_name} ({elapsed}ms): {err}")
        empty_sankey = {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}
        return APIResponse.ok(
            data=SankeyResponse(**empty_sankey),
            meta=meta,
        )
    return APIResponse.ok(data=SankeyResponse(**data), meta=meta)
