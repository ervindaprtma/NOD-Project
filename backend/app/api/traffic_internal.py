"""
Traffic Internal API routes (intra-lan + inter-site traffic).
Prefix: /api/v1/traffic-internal

All endpoints have try/except error handling to prevent 500 errors on
OpenSearch timeouts/connection issues.
"""
from __future__ import annotations

import time
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.api._safe import safe_query
from app.opensearch import traffic_internal as ti_qb
from app.schemas.common import APIResponse, Meta
from app.schemas.traffic_internal import (
    TrafficInternalSummaryResponse,
    TrafficInternalChartResponse,
    TrafficInternalTableResponse,
    SankeyResponse,
)

logger = logging.getLogger("nod.api.traffic_internal")
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
    traffic_path: str = Query("all", description="Traffic path filter: all, intra-lan, inter-site"),
    ingress_interface: str = Query("", description="Filter: ingress interface"),
    egress_interface: str = Query("", description="Filter: egress interface"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data, err = await safe_query(
        ti_qb.flow_summary,
        "traffic_internal.flow_summary",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, traffic_path=traffic_path,
        ingress_interface=ingress_interface, egress_interface=egress_interface,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = Meta(query_took_ms=elapsed)
    if data is None:
        empty = {
            "top_services": [], "top_clients": [], "top_servers": [],
            "ingress_breakdown": [], "egress_breakdown": [], "protocol_dist": [],
        }
        logger.warning(f"traffic-internal summary empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=TrafficInternalSummaryResponse(**empty), meta=meta)
    return APIResponse.ok(data=TrafficInternalSummaryResponse(**data), meta=meta)


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
    traffic_path: str = Query("all", description="Traffic path filter: all, intra-lan, inter-site"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data, err = await safe_query(
        ti_qb.flow_chart,
        "traffic_internal.flow_chart",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, bucket_seconds=bucket_seconds,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, traffic_path=traffic_path,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = Meta(query_took_ms=elapsed)
    if data is None:
        logger.warning(f"traffic-internal chart empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficInternalChartResponse(chart_data=[], service_names=[]),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficInternalChartResponse(**data), meta=meta)


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
    traffic_path: str = Query("all", description="Traffic path filter: all, intra-lan, inter-site"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    after_key: Optional[dict] = None
    if after:
        try: after_key = json.loads(after)
        except json.JSONDecodeError: return APIResponse.fail("INVALID_AFTER", "after must be valid JSON")
    data, err = await safe_query(
        ti_qb.flow_table,
        "traffic_internal.flow_table",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, after=after_key,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, traffic_path=traffic_path,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = Meta(query_took_ms=elapsed)
    if data is None:
        logger.warning(f"traffic-internal table empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficInternalTableResponse(records=[], after_key=None, total=0),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficInternalTableResponse(**data), meta=meta)


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
    traffic_path: str = Query("all", description="Traffic path filter: all, intra-lan, inter-site"),
    current_user=Depends(get_current_user),
):
    if site_name not in ALL_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALL_SITES)}")
    t0 = time.monotonic()
    data, err = await safe_query(
        ti_qb.sankey_data,
        "traffic_internal.sankey_data",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, traffic_path=traffic_path,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = Meta(query_took_ms=elapsed)
    if data is None:
        empty = {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}
        logger.warning(f"traffic-internal sankey empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=SankeyResponse(**empty), meta=meta)
    return APIResponse.ok(data=SankeyResponse(**data), meta=meta)
