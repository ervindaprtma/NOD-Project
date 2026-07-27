"""
VPN Sessions API (FR-01 panels P01-A, P01-B and dedicated VPN view).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.opensearch import ipsec as ipsec_qb
from app.opensearch import sslvpn as sslvpn_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import IPsecVPNUser, SSLVPNUser, VPNSessionHistoryItem

settings = get_settings()
router = APIRouter(prefix="/api/v1/vpn", tags=["VPN"])


def _fmt(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**2:.2f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


def _active_window(gte_ms: int, lte_ms: int) -> tuple[int, int]:
    """Clamp query window to last 60 seconds for 'currently active' VPN users.

    ponytail: If a user's latest document is older than 60s, they're
    considered disconnected. Data stores every 30s (Telegraf), so 60s
    = 2x scrape interval — catches at least one fresh document reliably.
    """
    return (max(gte_ms, lte_ms - 60_000), lte_ms)


@router.get("/ssl", response_model=APIResponse[list[SSLVPNUser]])
async def get_sslvpn_sessions(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    site_name: str = Query(default="Site_FGT-DC_SSLVPN", description="SSL VPN measurement_name"),
    current_user=Depends(get_current_user),
):
    """FR-01 P01-A detail: Active SSL VPN user sessions."""
    if site_name not in settings.sslvpn_sites_list:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown site: {site_name}. Configured: {settings.sslvpn_sites_list}",
        )

    t0 = time.monotonic()
    ag, al = _active_window(gte_ms, lte_ms)
    users = await sslvpn_qb.active_sslvpn_users(
        gte_ms=ag, lte_ms=al, site_name=site_name
    )
    elapsed = int((time.monotonic() - t0) * 1000)

    result = [
        SSLVPNUser(
            username=u["username"],
            device=u["device"],
            remote_ip=u["remote_ip"],
            vpn_ip=u["vpn_ip"],
            bytes_in=u["bytes_in"],
            bytes_out=u["bytes_out"],
            bytes_human_in=_fmt(u["bytes_in"]),
            bytes_human_out=_fmt(u["bytes_out"]),
        )
        for u in users
    ]
    return APIResponse.ok(data=result, meta={"query_took_ms": elapsed})


@router.get("/ipsec", response_model=APIResponse[list[IPsecVPNUser]])
async def get_ipsec_sessions(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """FR-01 P01-B detail: Active IPsec VPN user sessions."""
    t0 = time.monotonic()
    ag, al = _active_window(gte_ms, lte_ms)
    users = await ipsec_qb.active_ipsec_users_detail(gte_ms=ag, lte_ms=al)
    elapsed = int((time.monotonic() - t0) * 1000)

    result = [
        IPsecVPNUser(
            username=u["username"],
            device=u["device"],
            remote_gw_ip=u["remote_gw_ip"],
            assigned_ip=u["assigned_ip"],
            bytes_in=u["bytes_in"],
            bytes_out=u["bytes_out"],
            tunnel_lifetime_sec=u["tunnel_lifetime_sec"],
            bytes_human_in=_fmt(u["bytes_in"]),
            bytes_human_out=_fmt(u["bytes_out"]),
        )
        for u in users
    ]
    return APIResponse.ok(data=result, meta={"query_took_ms": elapsed})


@router.get("/sessions-history", response_model=APIResponse[list[VPNSessionHistoryItem]])
async def get_vpn_sessions_history(
    gte_ms: int = Query(..., description="Start of the selected range (epoch ms)"),
    lte_ms: int = Query(..., description="End of the selected range (epoch ms)"),
    site_name: str = Query(default="Site_FGT-DC_SSLVPN", description="SSL VPN measurement_name"),
    current_user=Depends(get_current_user),
):
    """VPN sessions for the day(s) the selected range covers, one row per session.

    A daily view: the range picks WHICH DAYS to show; every session on those days is
    returned (not just the ones live at a sub-day window edge — that hid earlier
    sessions when the page defaulted to a 15m preset). Sessions are cut at 00:00 WIB
    (never span midnight — rule 1), split on a >5min log gap (reconnect — rule 2), and
    end after >5min of silence (rule 3). We fetch from the WIB start-of-day of `gte_ms`
    so each session's TRUE start is visible; buckets coarsen on wide ranges for speed.
    """
    t0 = time.monotonic()

    wib = timezone(timedelta(hours=7))
    day_start = datetime.fromtimestamp(gte_ms / 1000, wib).replace(
        hour=0, minute=0, second=0, microsecond=0)
    fetch_gte = int(day_start.timestamp() * 1000)
    now_ms = int(datetime.now(wib).timestamp() * 1000)
    # 60s ≤ 24h span keeps minute precision; wider ranges use 2m to cap bucket count
    # (still fine for the 5min gap threshold), never coarser or reconnects blur.
    bucket = "60s" if (lte_ms - fetch_gte) <= 86_400_000 else "2m"

    ssl_hist, ipsec_hist = await asyncio.gather(
        sslvpn_qb.sslvpn_session_history(
            gte_ms=fetch_gte, lte_ms=lte_ms, site_name=site_name,
            now_ms=now_ms, bucket=bucket),
        ipsec_qb.ipsec_session_history(
            gte_ms=fetch_gte, lte_ms=lte_ms,
            now_ms=now_ms, bucket=bucket),
    )

    merged = [
        VPNSessionHistoryItem(
            username=h["username"],
            protocol="SSL VPN",
            site=site_name,
            session_started=h["session_started"],
            last_seen=h["last_seen"],
            bytes_in=h["bytes_in"],
            bytes_out=h["bytes_out"],
            status=h["status"],
        )
        for h in ssl_hist
    ] + [
        VPNSessionHistoryItem(
            username=h["username"],
            protocol="IPsec VPN",
            site="",
            session_started=h["session_started"],
            last_seen=h["last_seen"],
            bytes_in=h["bytes_in"],
            bytes_out=h["bytes_out"],
            status=h["status"],
        )
        for h in ipsec_hist
    ]

    merged.sort(key=lambda x: x.session_started, reverse=True)
    elapsed = int((time.monotonic() - t0) * 1000)
    return APIResponse.ok(data=merged, meta={"query_took_ms": elapsed})
