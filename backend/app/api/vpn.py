"""
VPN Sessions API (FR-01 panels P01-A, P01-B and dedicated VPN view).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.opensearch import ipsec as ipsec_qb
from app.opensearch import sslvpn as sslvpn_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import IPsecVPNUser, SSLVPNUser, VPNSessionsResponse

settings = get_settings()
router = APIRouter(prefix="/api/v1/vpn", tags=["VPN"])


def _fmt(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**3:.2f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


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
    users = await sslvpn_qb.active_sslvpn_users_detail(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name
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
    users = await ipsec_qb.active_ipsec_users_detail(gte_ms=gte_ms, lte_ms=lte_ms)
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
