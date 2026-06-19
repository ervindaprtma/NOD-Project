"""
Interface Stats API — per-interface throughput, speed, and operational status.
Dynamically discovers active production interfaces (filters out internal/virtual).
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.opensearch import interface_stats as iface_qb
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/v1/interface-stats", tags=["Interface Stats"])

# ── Inline Pydantic schemas ─────────────────────────────────────


class InterfaceTimelinePoint(BaseModel):
    """A single time-series point with throughput in Mbps.
    in_mbps / out_mbps are nullable — set to None when delta is negative (counter reset)."""
    timestamp: int
    in_mbps: Optional[float] = None
    out_mbps: Optional[float] = None


class InterfaceStatsItem(BaseModel):
    """Per-interface stats with current values and timeline."""
    if_index: str
    if_name: str
    label: str = ""                        # display label (ifAlias or ifName)
    current_in_mbps: Optional[float] = None
    current_out_mbps: Optional[float] = None
    speed_mbps: Optional[int] = None       # nominal interface speed
    oper_status: Optional[int] = None      # 1=UP, 2=DOWN
    timeline: list[InterfaceTimelinePoint] = []


class InterfaceStatsResponse(BaseModel):
    interfaces: list[InterfaceStatsItem]


# ── Helper: compute throughput deltas ────────────────────────────


def _compute_throughput_timeline(
    time_buckets: list[dict],
) -> list[InterfaceTimelinePoint]:
    """
    Convert cumulative counter per-bucket into Mbps throughput.
    throughputMbps = (max_current - max_prev) × 8 / 60 / 1_000_000
    If delta < 0 (counter reset), return None for that bucket.
    """
    points: list[InterfaceTimelinePoint] = []
    prev_in: Optional[float] = None
    prev_out: Optional[float] = None

    for bucket in time_buckets:
        ts = bucket["key"]
        max_in = bucket.get("max_in_octets", {}).get("value")
        max_out = bucket.get("max_out_octets", {}).get("value")

        in_mbps: Optional[float] = None
        out_mbps: Optional[float] = None

        if max_in is not None and prev_in is not None:
            delta = max_in - prev_in
            if delta >= 0:
                in_mbps = round(delta * 8 / 60 / 1_000_000, 4)

        if max_out is not None and prev_out is not None:
            delta = max_out - prev_out
            if delta >= 0:
                out_mbps = round(delta * 8 / 60 / 1_000_000, 4)

        points.append(InterfaceTimelinePoint(
            timestamp=ts,
            in_mbps=in_mbps,
            out_mbps=out_mbps,
        ))

        prev_in = max_in
        prev_out = max_out

    return points


def _pick_latest_value(timeline: list[InterfaceTimelinePoint], attr: str) -> Optional[float]:
    """Pick the most recent non-None throughput value from the timeline."""
    for pt in reversed(timeline):
        val = getattr(pt, attr, None)
        if val is not None:
            return val
    return None


# ── Endpoint ─────────────────────────────────────────────────────


@router.get("", response_model=APIResponse[InterfaceStatsResponse])
async def get_interface_stats(
    site_name: str = Query(..., description="Site name: Site_FGT-DC, Site_FGT-DRC, or Site_FGT_Office"),
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """
    Per-interface throughput, speed, and operational status for a given site.

    Dynamically discovers active production interfaces by filtering:
    - ifOperStatus >= 1 (UP)
    - Excluding internal/virtual interfaces (mgmt, ha, ssl.*, fortilink, etc.)

    Returns per-interface data at 60-second intervals.
    """
    t0 = time.monotonic()

    # Validate site_name
    valid_sites = set(iface_qb.SITE_SOURCE_MAP.keys())
    if site_name not in valid_sites:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown site: {site_name}. Valid sites: {', '.join(sorted(valid_sites))}",
        )

    # Q-07: single OpenSearch query for all interfaces
    raw_aggs = await iface_qb.interface_stats_timeline(
        gte_ms=gte_ms,
        lte_ms=lte_ms,
        site_name=site_name,
    )

    interfaces: list[InterfaceStatsItem] = []
    iface_labels = iface_qb.SITE_IFINDEX_MAP.get(site_name, {})
    sort_order = iface_qb.SITE_IFACE_SORT_ORDER.get(site_name, {})

    for iface_bucket in raw_aggs.get("by_interface", {}).get("buckets", []):
        if_index = iface_bucket["key"]

        # Use hardcoded label from SITE_IFINDEX_MAP
        label = iface_labels.get(if_index, f"Interface {if_index}")

        # Time buckets
        time_buckets = iface_bucket.get("by_time", {}).get("buckets", [])

        # Compute throughput deltas
        timeline = _compute_throughput_timeline(time_buckets)

        # Extract last bucket's speed and oper_status
        speed_mbps = None
        oper_status = None
        for bucket in reversed(time_buckets):
            sv = bucket.get("speed_mbps", {}).get("value")
            ov = bucket.get("oper_status", {}).get("value")
            if sv is not None:
                speed_mbps = int(sv)
            if ov is not None:
                oper_status = int(ov)
            if speed_mbps is not None or oper_status is not None:
                break

        current_in_mbps = _pick_latest_value(timeline, "in_mbps")
        current_out_mbps = _pick_latest_value(timeline, "out_mbps")

        interfaces.append(InterfaceStatsItem(
            if_index=if_index,
            if_name=if_index,
            label=label,
            current_in_mbps=current_in_mbps,
            current_out_mbps=current_out_mbps,
            speed_mbps=speed_mbps,
            oper_status=oper_status,
            timeline=timeline,
        ))

    # Sort by defined order (WAN first, MPLS second; vendor grouping)
    interfaces.sort(key=lambda x: sort_order.get(x.if_index, 99))

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=InterfaceStatsResponse(interfaces=interfaces),
        meta={"query_took_ms": elapsed},
    )
