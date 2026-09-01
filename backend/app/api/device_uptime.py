"""
Device Availability API — per-device uptime, availability %, and reboot history.

Reads the telegraf `device_uptime` measurement and reports availability from the
SNMP `sys_uptime` counter (design_availability_devices.md §9): the counter is
ground truth for whether the device was up, so a Telegraf timeout or packet loss
cannot dent it — only a reboot (counter reset) or a device that goes silent and
never returns is charged as downtime.

Availability is reported PER DEVICE. There is deliberately no fleet-average
percentage: averaging a device onboarded two hours ago against one up for fifty
days describes nothing. The summary carries counts plus the lowest-uptime device
(the most recently booted) instead.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api._safe import build_meta, safe_query
from app.api.auth import get_current_user
from app.opensearch import device_uptime as du_qb
from app.opensearch.query import track_degradation
from app.schemas.common import APIResponse

logger = logging.getLogger("nod.api")

router = APIRouter(prefix="/api/v1/device-uptime", tags=["Device Availability"])

# ── Inline Pydantic schemas ─────────────────────────────────────


class DeviceAvailabilityPoint(BaseModel):
    """One histogram bucket. `uptime_seconds` is None when no poll landed in it;
    `availability_pct` is None where the device could not have reported at all
    (before it existed, or outside the window) — the chart breaks the line there
    rather than drawing a misleading 0%."""
    ts_ms: int
    uptime_seconds: Optional[float] = None
    polls: int = 0
    expected: int = 0
    availability_pct: Optional[float] = None
    reboot: bool = False
    collector_gap: bool = False


class RebootEvent(BaseModel):
    """`note` is set for a 32-bit counter wrap, which is NOT an outage."""
    at_ms: int
    downtime_seconds: int = 0
    note: Optional[str] = None


class CollectorGap(BaseModel):
    """A window where every device at the site went silent — see summary docs."""
    start_ms: int
    end_ms: int
    duration_seconds: int


class DeviceAvailabilityItem(BaseModel):
    device_key: str                        # tag.source (IP) — hostnames get renamed, IPs don't
    hostname: str = ""
    vendor: str = ""
    site: str = ""
    status: str = "up"                     # up | rebooted | not_reporting | collector_gap
    sys_uptime_ticks: int = 0
    uptime_seconds: float = 0.0
    uptime_human_long: str = ""            # "7 days 10 hours 5 minutes"
    uptime_human_short: str = ""           # "7d 10h"
    boot_time_ms: int = 0
    first_seen_ms: Optional[int] = None
    last_seen_ms: Optional[int] = None
    partial_history: bool = False          # onboarded mid-window — % is clamped to its own span
    # Site migration (plan §5): set when the device's tag.site changed inside the
    # window and the prior era was stitched into this response. Null otherwise.
    site_moved_from: Optional[str] = None  # normalized previous site_tag (e.g. "dc")
    site_moved_at_ms: Optional[int] = None # era boundary — last doc in the old site
    # Re-IP (device_ip_aliases): set when the device's tag.source changed inside
    # the window and the old-IP era was merged into this card. Null otherwise.
    reip_from: Optional[list[str]] = None  # old source IP(s) now aliasing to this card
    reip_at_ms: Optional[int] = None       # era boundary — first doc under the current IP
    wrap_risk: bool = False                # uptime near the 497-day counter wrap
    availability_pct: Optional[float] = None   # None = unknown, never a fabricated estimate
    expected_polls: int = 0
    successful_polls: int = 0
    excluded_collector_seconds: int = 0
    reboots: list[RebootEvent] = []
    reboot_count: int = 0
    total_downtime_seconds: int = 0
    series: list[DeviceAvailabilityPoint] = []


class DeviceAvailabilitySummary(BaseModel):
    window: str = ""
    window_seconds: int = 0
    site: str = ""
    devices_total: int = 0
    devices_reporting: int = 0
    devices_partial_history: int = 0
    devices_with_reboots: int = 0
    lowest_uptime_device: Optional[dict] = None  # {hostname, uptime_seconds, uptime_human_short} — most recently booted
    reboots_total: int = 0
    collector_gap_seconds: int = 0
    collector_gaps: list[CollectorGap] = []
    history_start_ms: Optional[int] = None
    history_sufficient: bool = False       # False → window reaches before the data does


class DeviceAvailabilityResponse(BaseModel):
    summary: DeviceAvailabilitySummary
    devices: list[DeviceAvailabilityItem]


_EMPTY = DeviceAvailabilityResponse(summary=DeviceAvailabilitySummary(), devices=[])


# ── Endpoint ─────────────────────────────────────────────────────


@router.get("", response_model=APIResponse[DeviceAvailabilityResponse])
async def get_device_availability(
    site_name: str = Query(..., description="Site_FGT-DC, Site_FGT-DRC, or Site_FGT_Office"),
    window: str = Query("24h", description="Reporting window: 24h, 7d, 30d, 90d, 365d"),
    gte_ms: Optional[int] = Query(None, description="Start (epoch ms) — overrides window"),
    lte_ms: Optional[int] = Query(None, description="End (epoch ms) — overrides window"),
    current_user=Depends(get_current_user),
):
    """
    Per-device availability for one site.

    `window` names the reporting range and sets the SLA denominator. Explicit
    `gte_ms`/`lte_ms` override it — that is how drag-to-zoom asks for a sub-range
    without changing the headline window.

    Availability is counter-based `(window − downtime) / window`, where downtime is
    only reboots (counter resets) and never-returned outages — a missed poll whose
    counter kept climbing is not downtime. Clamped to each device's own first
    sample so a newly onboarded device isn't reported as an outage.
    """
    valid_sites = set(du_qb.SITE_TAG.keys())
    if site_name not in valid_sites:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown site: {site_name}. Valid sites: {', '.join(sorted(valid_sites))}",
        )
    if window not in du_qb.WINDOW_SECONDS:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown window: {window}. Valid: {', '.join(du_qb.WINDOW_SECONDS)}",
        )

    t0 = time.monotonic()
    degraded = track_degradation()
    data, err = await safe_query(
        du_qb.device_availability,
        "device_availability",
        site_name=site_name,
        window=window,
        gte_ms=gte_ms,
        lte_ms=lte_ms,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)

    if data is None:
        # Empty payload, never a 500 — the UI shows "no data", and meta.degraded
        # tells it this is "unknown" rather than "no devices".
        logger.warning(f"device availability empty for {site_name} ({elapsed}ms): {err}")
        return APIResponse.ok(data=_EMPTY, meta=meta)

    return APIResponse.ok(data=DeviceAvailabilityResponse(**data), meta=meta)
