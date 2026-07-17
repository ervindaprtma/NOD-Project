"""
Traffic Inbound API routes (VIP Inbound traffic analytics).
Prefix: /api/v1/traffic-inbound

Endpoints:
  GET /summary  — All widgets (port/service-based)
  GET /chart    — 60s stacked bar chart data
  GET /table    — Paginated flow records table
  GET /sankey   — Sankey diagram nodes+links

All endpoints have try/except error handling to prevent 500 errors.
"""
from __future__ import annotations

import time
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch.query import track_degradation
from app.api._safe import build_meta, safe_query
from app.opensearch import traffic_inbound as ti_qb
from app.schemas.common import APIResponse
from app.schemas.traffic_inbound import (
    TrafficInboundSummaryResponse,
    TrafficInboundChartResponse,
    TrafficInboundTableResponse,
    SankeyResponse,
)

logger = logging.getLogger("nod.api.traffic_inbound")
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
    src_as_org: str = Query("", description="Filter: source AS org (comma-separated)"),
    ingress_interface: str = Query("", description="Filter: ingress interface"),
    egress_interface: str = Query("", description="Filter: egress interface"),
    current_user=Depends(get_current_user),
):
    """Returns all traffic inbound widget data (service/port-based)."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        ti_qb.flow_summary,
        "traffic_inbound.flow_summary",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter="inbound-vip",
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, src_as_org=src_as_org,
        ingress_interface=ingress_interface, egress_interface=egress_interface,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        empty = {
            "total_bytes": 0, "total_upload": 0, "total_download": 0, "total_sessions": 0,
            "top_services": [], "top_src_as_org": [], "top_src_as_country": [],
            "top_clients": [], "top_servers": [],
            "protocol_dist": [], "egress_breakdown": [], "ingress_breakdown": [],
        }
        logger.warning(f"traffic-inbound summary empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=TrafficInboundSummaryResponse(**empty), meta=meta)
    return APIResponse.ok(data=TrafficInboundSummaryResponse(**data), meta=meta)


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
    src_as_org: str = Query("", description="Filter: source AS org (comma-separated)"),
    current_user=Depends(get_current_user),
):
    """Returns stacked bar chart for service throughput (port-based)."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        ti_qb.flow_chart,
        "traffic_inbound.flow_chart",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        path_filter="inbound-vip", bucket_seconds=bucket_seconds,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, src_as_org=src_as_org,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        logger.warning(f"traffic-inbound chart empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficInboundChartResponse(chart_data=[], service_names=[]),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficInboundChartResponse(**data), meta=meta)


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
    src_as_org: str = Query("", description="Filter: source AS org (comma-separated)"),
    current_user=Depends(get_current_user),
):
    """Returns paginated inbound flow records with composite aggregation."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")
    t0 = time.monotonic()
    degraded = track_degradation()
    after_key: Optional[dict] = None
    if after:
        try:
            after_key = json.loads(after)
        except json.JSONDecodeError:
            return APIResponse.fail("INVALID_AFTER", "after parameter must be valid JSON")
    data, err = await safe_query(
        ti_qb.flow_table,
        "traffic_inbound.flow_table",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name,
        after=after_key, path_filter="inbound-vip",
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, src_as_org=src_as_org,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        logger.warning(f"traffic-inbound table empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(
            data=TrafficInboundTableResponse(records=[], after_key=None),
            meta=meta,
        )
    return APIResponse.ok(data=TrafficInboundTableResponse(**data), meta=meta)


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
    src_as_org: str = Query("", description="Filter: source AS org (comma-separated)"),
    current_user=Depends(get_current_user),
):
    """Returns Sankey diagram nodes+links. direction='' for unfiltered, 'upload' or 'download' for zone-based direction."""
    if site_name not in ALLOWED_SITES:
        return APIResponse.fail("INVALID_SITE", f"Site must be one of: {', '.join(ALLOWED_SITES)}")
    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        ti_qb.sankey_data,
        "traffic_inbound.sankey_data",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, path_filter="inbound-vip",
        direction=direction,
        app_filter=app_filter, client_ip=client_ip, server_ip=server_ip,
        protocol=protocol, dst_port=dst_port, src_as_org=src_as_org,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if data is None:
        empty = {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}
        logger.warning(f"traffic-inbound sankey empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=SankeyResponse(**empty), meta=meta)
    return APIResponse.ok(data=SankeyResponse(**data), meta=meta)
