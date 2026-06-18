"""
Overview dashboard response schemas.
Maps aggregations from OpenSearch to typed Pydantic models.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── P01-A | P01-B: Active VPN Users ────────────────────────────

class ActiveUserKPI(BaseModel):
    active_users: int
    label: str  # "SSL VPN" or "IPsec VPN"


# ── P01-C/D/E/F: FortiGate Resources ──────────────────────────

class SparklinePoint(BaseModel):
    timestamp: int
    value: float


class DeviceResourceStatus(BaseModel):
    device: str
    hostname: Optional[str] = None
    cpu_usage: Optional[float] = None
    mem_usage: Optional[float] = None
    session_count: Optional[int] = None
    sync_status: Optional[str] = None
    session_sparkline: list[SparklinePoint] = []


# ── P01-G: Top Applications ────────────────────────────────────

class TopApplication(BaseModel):
    application: str
    total_bytes: int
    bytes_human: str


# ── Top Destination AS Organizations ──────────────────────────

class TopASOrg(BaseModel):
    org_name: str
    total_bytes: int
    bytes_human: str


# ── P01-H: SD-WAN Link Status ──────────────────────────────────

class WanLinkStatus(BaseModel):
    link: str
    link_name: str
    status: str  # "Up" | "Down" | "Degraded"


class SiteWanStatus(BaseModel):
    site: str
    device: Optional[str] = None
    links: list[WanLinkStatus]


# ── P01-I: Total Throughput ────────────────────────────────────

class ThroughputKPI(BaseModel):
    total_bytes: int
    bytes_human: str


# ── HA Status ──────────────────────────────────────────────────

class HAStatusKPI(BaseModel):
    ha_mode: str
    member_count: int
    overall_health: str  # "healthy" | "degraded" | "critical"


# ── WAN Interface Bandwidth ────────────────────────────────────

class WanInterfaceSummary(BaseModel):
    label: str
    in_mbps: Optional[float] = None
    out_mbps: Optional[float] = None
    speed_mbps: Optional[int] = None
    oper_status: Optional[str] = None  # "UP" | "DOWN"


class SiteWanBandwidth(BaseModel):
    site: str
    interfaces: list[WanInterfaceSummary]


# ── Inbound VIP Summary ────────────────────────────────────────

class TopInboundService(BaseModel):
    service_name: str
    total_bytes: int
    bytes_human: str


# ── Complete Overview Response ─────────────────────────────────

class OverviewResponse(BaseModel):
    ssl_vpn_users: ActiveUserKPI
    ipsec_vpn_users: ActiveUserKPI
    devices: list[DeviceResourceStatus]
    top_applications: list[TopApplication]
    top_dst_as_orgs: list[TopASOrg] = []
    sdwan_sites: list[SiteWanStatus]
    total_throughput: ThroughputKPI
    ha_status: Optional[HAStatusKPI] = None
    wan_bandwidth: list[SiteWanBandwidth] = []
    inbound_vip_services: list[TopInboundService] = []
    active_alert_count: int = 0
