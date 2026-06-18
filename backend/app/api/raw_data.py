"""
Raw Data Table API (FR-05).
Implements server-side paginated, filterable raw flow records.
Q-08: Uses search_after for pagination; from+size capped at 10,000.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.auth import get_current_user, require_role
from app.opensearch import appid as appid_qb
from app.schemas.common import APIResponse, Meta
from app.schemas.traffic import RawFlowRecord

router = APIRouter(prefix="/api/v1/traffic", tags=["Raw Data"])


def _fmt(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**3:.2f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


@router.get("/raw", response_model=APIResponse[list[RawFlowRecord]])
async def get_raw_flows(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    page_size: int = Query(default=25, ge=1, le=500, description="Rows per page (max 500)"),
    search_after_timestamp: Optional[int] = Query(default=None, description="search_after: @timestamp value"),
    search_after_id: Optional[str] = Query(default=None, description="search_after: _id tiebreaker"),
    sort_by: Optional[str] = Query(default="@timestamp", description="Sort column"),
    sort_dir: Optional[str] = Query(default="desc", pattern=r"^(asc|desc)$"),
    client_ip: Optional[str] = Query(default=None),
    server_ip: Optional[str] = Query(default=None),
    application: Optional[str] = Query(default=None, description="Comma-separated application names"),
    category: Optional[str] = Query(default=None, description="Comma-separated categories"),
    protocol: Optional[str] = Query(default=None, description="Comma-separated protocols"),
    dst_port: Optional[int] = Query(default=None),
    ingress_zone: Optional[str] = Query(default=None),
    egress_link: Optional[str] = Query(default=None),
    site_name: str = Query(default="Site_FGT-DC", description="Site: Site_FGT-DC, Site_FGT-DRC, Site_FGT_Office"),
    current_user=Depends(require_role("operator")),
):
    """
    FR-05: Server-side paginated raw flow records.
    Q-04: Uses search_after, not scroll.
    Q-03: _source includes only table columns.
    Q-08: Page size capped at 500.
    """
    t0 = time.monotonic()

    # Build filters dict
    filters: dict = {}
    if client_ip:
        filters["client_ip"] = client_ip
    if server_ip:
        filters["server_ip"] = server_ip
    if application:
        filters["application"] = [a.strip() for a in application.split(",") if a.strip()]
    if category:
        filters["category"] = [c.strip() for c in category.split(",") if c.strip()]
    if protocol:
        filters["protocol"] = [p.strip() for p in protocol.split(",") if p.strip()]
    if dst_port is not None:
        filters["dst_port"] = dst_port
    if ingress_zone:
        filters["ingress_zone"] = [z.strip() for z in ingress_zone.split(",") if z.strip()]
    if egress_link:
        filters["egress_link"] = [l.strip() for l in egress_link.split(",") if l.strip()]

    # Build search_after array
    sa = None
    if search_after_timestamp is not None and search_after_id is not None:
        sa = [search_after_timestamp, search_after_id]

    result = await appid_qb.raw_flows(
        gte_ms=gte_ms,
        lte_ms=lte_ms,
        page_size=page_size,
        search_after=sa,
        sort_by=sort_by,
        sort_dir=sort_dir,
        filters=filters,
        site_name=site_name,
    )

    elapsed = int((time.monotonic() - t0) * 1000)

    records = [
        RawFlowRecord(
            timestamp=r["timestamp"],
            client_ip=r["client_ip"],
            server_ip=r["server_ip"],
            application=r["application"],
            category=r["category"],
            protocol=r["protocol"],
            dst_port=r["dst_port"],
            total_bytes=r["total_bytes"],
            bytes_human=_fmt(r["total_bytes"]),
            packets=r["packets"],
            ingress_zone=r["ingress_zone"],
            egress_link=r["egress_link"],
            correlation_id=r.get("correlation_id"),
            correlation_direction=r.get("correlation_direction"),
        )
        for r in result["records"]
    ]

    return APIResponse.ok(
        data=records,
        meta=Meta(
            total=result["total_hits"],
            page_size=page_size,
            query_took_ms=elapsed,
        ),
    )
