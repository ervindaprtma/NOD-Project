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
    in_mbps / out_mbps are nullable — None on a counter reset (negative delta) or a
    data-loss gap boundary (breaks the line so the chart shows no fake spike)."""
    timestamp: int
    in_mbps: Optional[float] = None
    out_mbps: Optional[float] = None


class TimelineGap(BaseModel):
    """A data-loss window — collection was down, so no bytes were observed between
    start_ms and end_ms. The chart marks it (shaded band) instead of drawing a rate."""
    start_ms: int
    end_ms: int


class InterfaceStatsItem(BaseModel):
    """Per-interface stats averaged over the queried window, plus the timeline."""
    if_index: str
    if_name: str
    label: str = ""                        # display label (ifAlias or ifName)
    avg_in_mbps: Optional[float] = None     # mean rate across the window, not the latest sample
    avg_out_mbps: Optional[float] = None
    peak_in_mbps: Optional[float] = None    # busiest single bucket
    peak_out_mbps: Optional[float] = None
    last_in_mbps: Optional[float] = None    # final bucket — a bucket average, not a live reading
    last_out_mbps: Optional[float] = None
    total_in_bytes: float = 0              # cumulative volume over the window (sum of per-bucket deltas)
    total_out_bytes: float = 0
    speed_mbps: Optional[int] = None       # nominal interface speed
    oper_status: Optional[int] = None      # 1=UP, 2=DOWN
    timeline: list[InterfaceTimelinePoint] = []
    gaps: list[TimelineGap] = []           # data-loss windows to mark on the chart


class InterfaceStatsResponse(BaseModel):
    interfaces: list[InterfaceStatsItem]


# ── Helper: compute throughput deltas ────────────────────────────


# A jump between two consecutive returned buckets larger than this many intervals
# means buckets were dropped in between — the date_histogram uses min_doc_count=1, so a
# data-loss window (OpenSearch down / Telegraf not writing) collapses to nothing and the
# next bucket carries the WHOLE outage's counter delta. Dividing that by one interval was
# the "83× spike" bug. 1.5 tolerates normal jitter but catches any real gap.
_GAP_FACTOR = 1.5


def _compute_throughput_timeline(
    time_buckets: list[dict],
    interval_seconds: int = 60,
    lte_ms: Optional[int] = None,
) -> tuple[list[InterfaceTimelinePoint], float, float, list["TimelineGap"]]:
    """
    Convert cumulative counter per-bucket into Mbps throughput.
    throughputMbps = (max_current - max_prev) × 8 / interval_seconds / 1_000_000
    If delta < 0 (counter reset), return None for that bucket.

    The first bucket with counter values is used as baseline only — it has no
    prior counter so no delta can be computed, and it is skipped to avoid
    a misleading zero point at the chart boundary.

    The trailing bucket is dropped when it is a partial (in-progress) interval:
    a bucket starting at `key` covers [key, key+interval); if it extends past
    lte_ms it only captured a slice of the interval, so dividing its small delta
    by the full interval_seconds yields a fake ~0 rate — the "drop to 0 at the
    end of the chart" artifact. Historical ranges that end on a bucket boundary
    keep all buckets.

    **Data-loss gaps.** When two consecutive buckets are more than _GAP_FACTOR
    intervals apart, buckets were dropped between them (collection was down). The
    counter delta across that span is real bytes but spread over the whole gap, so
    it is NOT a valid per-interval rate — emitting it divided by one interval was
    the 83× false spike. Such a bucket emits a null point (breaks the line), the
    baseline is re-seeded from it, its delta is excluded from the volume totals, and
    the span is recorded in the returned gaps list so the chart can mark it.

    Returns (points, total_in_bytes, total_out_bytes, gaps).
    """
    if lte_ms is not None:
        interval_ms = interval_seconds * 1000
        while time_buckets and time_buckets[-1]["key"] + interval_ms > lte_ms:
            time_buckets = time_buckets[:-1]

    points: list[InterfaceTimelinePoint] = []
    gaps: list[TimelineGap] = []
    prev_in: Optional[float] = None
    prev_out: Optional[float] = None
    prev_ts: Optional[int] = None
    started: bool = False
    total_in_bytes: float = 0
    total_out_bytes: float = 0
    gap_threshold_ms = interval_seconds * 1000 * _GAP_FACTOR

    for bucket in time_buckets:
        ts = bucket["key"]
        max_in = bucket.get("max_in_octets", {}).get("value")
        max_out = bucket.get("max_out_octets", {}).get("value")

        # First bucket with counter values — seed the baseline, skip emitting
        if not started:
            prev_in, prev_out, prev_ts = max_in, max_out, ts
            if max_in is not None or max_out is not None:
                started = True
            continue

        # Data-loss gap: buckets were dropped between prev_ts and ts. The delta here
        # spans the whole outage — break the line, re-seed, record the gap, and DON'T
        # charge it to one interval (the spike) or to the volume totals.
        if prev_ts is not None and (ts - prev_ts) > gap_threshold_ms:
            gaps.append(TimelineGap(start_ms=prev_ts, end_ms=ts))
            points.append(InterfaceTimelinePoint(timestamp=ts, in_mbps=None, out_mbps=None))
            prev_in, prev_out, prev_ts = max_in, max_out, ts
            continue

        in_mbps: Optional[float] = None
        out_mbps: Optional[float] = None

        if max_in is not None and prev_in is not None:
            delta = max_in - prev_in
            if delta >= 0:
                in_mbps = round(delta * 8 / interval_seconds / 1_000_000, 4)
                total_in_bytes += delta

        if max_out is not None and prev_out is not None:
            delta = max_out - prev_out
            if delta >= 0:
                out_mbps = round(delta * 8 / interval_seconds / 1_000_000, 4)
                total_out_bytes += delta

        points.append(InterfaceTimelinePoint(
            timestamp=ts,
            in_mbps=in_mbps,
            out_mbps=out_mbps,
        ))

        prev_in, prev_out, prev_ts = max_in, max_out, ts

    return points, total_in_bytes, total_out_bytes, gaps


def _summarize(
    timeline: list[InterfaceTimelinePoint], attr: str
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    (avg, peak, last) throughput across the window, skipping buckets with no
    delta — counter resets and the seeded first bucket emit None, and max()
    would raise on them.

    All three are per-bucket rates, so they scale with the adaptive interval:
    a 24h peak is the busiest 15-minute average, not the busiest instant.
    """
    vals = [v for pt in timeline if (v := getattr(pt, attr, None)) is not None]
    if not vals:
        return None, None, None
    return round(sum(vals) / len(vals), 2), round(max(vals), 2), round(vals[-1], 2)


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

    Returns per-interface data at adaptive intervals (60s/5m/15m based on time range).
    """
    t0 = time.monotonic()

    # Validate site_name
    valid_sites = set(iface_qb.SITE_SOURCE_MAP.keys())
    if site_name not in valid_sites:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown site: {site_name}. Valid sites: {', '.join(sorted(valid_sites))}",
        )

    # Compute dynamic interval based on time range
    delta_sec = (lte_ms - gte_ms) / 1000
    if delta_sec <= 7200:
        iface_interval = "60s"
        interval_seconds = 60
    elif delta_sec <= 43200:
        iface_interval = "5m"
        interval_seconds = 300
    else:
        iface_interval = "15m"
        interval_seconds = 900

    # Q-07: single OpenSearch query for all interfaces
    result = await iface_qb.interface_stats_timeline(
        gte_ms=gte_ms,
        lte_ms=lte_ms,
        site_name=site_name,
        interval=iface_interval,
    )
    raw_aggs = result.get("aggregations", {})

    interfaces: list[InterfaceStatsItem] = []
    iface_labels = iface_qb.SITE_IFINDEX_MAP.get(site_name, {})
    sort_order = iface_qb.SITE_IFACE_SORT_ORDER.get(site_name, {})

    for iface_bucket in raw_aggs.get("by_interface", {}).get("buckets", []):
        if_index = iface_bucket["key"]

        # Use hardcoded label from SITE_IFINDEX_MAP
        label = iface_labels.get(if_index, f"Interface {if_index}")

        # Time buckets
        time_buckets = iface_bucket.get("by_time", {}).get("buckets", [])

        # Compute throughput deltas + cumulative volume + data-loss gaps
        timeline, total_in_bytes, total_out_bytes, gaps = _compute_throughput_timeline(
            time_buckets, interval_seconds=interval_seconds, lte_ms=lte_ms
        )

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

        avg_in_mbps, peak_in_mbps, last_in_mbps = _summarize(timeline, "in_mbps")
        avg_out_mbps, peak_out_mbps, last_out_mbps = _summarize(timeline, "out_mbps")

        interfaces.append(InterfaceStatsItem(
            if_index=if_index,
            if_name=if_index,
            label=label,
            avg_in_mbps=avg_in_mbps,
            avg_out_mbps=avg_out_mbps,
            peak_in_mbps=peak_in_mbps,
            peak_out_mbps=peak_out_mbps,
            last_in_mbps=last_in_mbps,
            last_out_mbps=last_out_mbps,
            total_in_bytes=total_in_bytes,
            total_out_bytes=total_out_bytes,
            speed_mbps=speed_mbps,
            oper_status=oper_status,
            timeline=timeline,
            gaps=gaps,
        ))

    # Sort by defined order (WAN first, MPLS second; vendor grouping)
    interfaces.sort(key=lambda x: sort_order.get(x.if_index, 99))

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=InterfaceStatsResponse(interfaces=interfaces),
        meta={"query_took_ms": elapsed},
    )
