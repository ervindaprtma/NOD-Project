"""
SD-WAN SLA, Resource, and VPN schemas.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── SD-WAN SLA (FR-03) ─────────────────────────────────────────

# Per-site link label mapping: {site_name: {linkN: display_label}}
SITE_LINK_LABELS: dict[str, dict[str, str]] = {
    "Site_FGT-DC": {
        "link1": "WAN LinkNet",
        "link2": "WAN iForte",
        "link3": "MPLS LinkNet",
        "link4": "MPLS iForte",
    },
    "Site_FGT-DRC": {
        "link1": "WAN LinkNet",
        "link2": "WAN iForte",
        "link3": "MPLS iForte",
        "link4": "MPLS LinkNet",
    },
    "Site_FGT_Office": {
        "link1": "WAN LDP",
        "link2": "WAN iForte",
        "link3": "MPLS iForte",
        "link4": "MPLS LinkNet",
    },
}

# How many links each site has for monitoring (default 4)
SITE_LINK_COUNT: dict[str, int] = {
    "Site_FGT-DC": 4,
    "Site_FGT-DRC": 4,
    "Site_FGT_Office": 4,
}

# Which OpenSearch endpoint each site's SD-WAN data lives on.
# "dc"  = OPENSEARCH_DC_URL  (10.80.150.108)
# "drc" = OPENSEARCH_DRC_URL  (10.90.150.108)
SITE_OS_ENDPOINT: dict[str, str] = {
    "Site_FGT-DC": "dc",
    "Site_FGT-DRC": "drc",
    "Site_FGT_Office": "dc",
}
SITE_LINK_TYPES: dict[str, dict[str, str]] = {
    "Site_FGT-DC": {"link1": "WAN", "link2": "WAN", "link3": "MPLS", "link4": "MPLS"},
    "Site_FGT-DRC": {"link1": "WAN", "link2": "WAN", "link3": "MPLS", "link4": "MPLS"},
    "Site_FGT_Office": {"link1": "WAN", "link2": "WAN", "link3": "MPLS", "link4": "MPLS"},
}

class LinkMetricPoint(BaseModel):
    timestamp: int  # epoch ms
    value: float


class SLATimeline(BaseModel):
    link1: list[LinkMetricPoint]
    link2: list[LinkMetricPoint]


class LinkCurrentStatus(BaseModel):
    link: str
    ifname: str
    status: str  # Up / Down / Degraded
    sla_target: str


class SiteSLAStatus(BaseModel):
    site: str
    device: Optional[str] = None
    links: list[LinkCurrentStatus]


class SLASummaryKPI(BaseModel):
    avg_latency_link1: float
    avg_latency_link2: float
    max_latency_link1: float
    max_latency_link2: float
    avg_jitter_link1: float
    avg_jitter_link2: float
    avg_packet_loss_link1: float
    avg_packet_loss_link2: float


class LinkMetricPoint(BaseModel):
    timestamp: int  # epoch ms
    value: float
    label: str  # display label e.g. "WAN LinkNet"
    link_type: str = "WAN"  # "WAN" or "MPLS"


class SLATimeline(BaseModel):
    links: list[LinkMetricPoint]  # flattened list, filtered by link_type on frontend


class LinkCurrentStatus(BaseModel):
    link: str  # link1, link2, link3, link4
    ifname: str  # actual interface name
    label: str  # display label
    link_type: str  # WAN or MPLS
    status: str  # Up / Down
    sla_target: str


class SiteSLAStatus(BaseModel):
    site: str
    device: Optional[str] = None
    links: list[LinkCurrentStatus]


class SLASummaryKPI(BaseModel):
    avg_latency: list[float]  # per link
    max_latency: list[float]
    avg_jitter: list[float]
    avg_packet_loss: list[float]
    labels: list[str]  # display labels for each link
    link_types: list[str]  # WAN or MPLS for each link


class SDWANResponse(BaseModel):
    latency_timeline: SLATimeline
    jitter_timeline: SLATimeline
    packet_loss_timeline: SLATimeline
    link_status: list[SiteSLAStatus]
    summary: SLASummaryKPI
    source_ip: str = ""


# ── Resource View (FR-04) ──────────────────────────────────────

class ResourcePoint(BaseModel):
    timestamp: int  # epoch ms
    value: float
    device: str


class ResourceTimeline(BaseModel):
    cpu: list[ResourcePoint]
    memory: list[ResourcePoint]
    sessions: list[ResourcePoint]


class DeviceCurrentResource(BaseModel):
    device: str
    hostname: Optional[str] = None
    serial_number: Optional[str] = None
    cpu_usage: float
    mem_usage: float
    session_count: int
    sync_status: str  # "In Sync" | "Out of Sync" | "Unknown" | "standalone"
    mem_capacity_kb: Optional[int] = None


class ResourceResponse(BaseModel):
    timeline: ResourceTimeline
    current: list[DeviceCurrentResource]


# ── VPN Sessions ───────────────────────────────────────────────

class SSLVPNUser(BaseModel):
    username: str
    device: str
    remote_ip: str
    vpn_ip: str
    bytes_in: int
    bytes_out: int
    bytes_human_in: str
    bytes_human_out: str


class IPsecVPNUser(BaseModel):
    username: str
    device: str
    remote_gw_ip: str
    assigned_ip: str
    bytes_in: int
    bytes_out: int
    tunnel_lifetime_sec: int
    bytes_human_in: str
    bytes_human_out: str


class VPNSessionsResponse(BaseModel):
    ssl_vpn: list[SSLVPNUser]
    ipsec_vpn: list[IPsecVPNUser]


# ── HA Cluster Status ───────────────────────────────────────────

class HAMember(BaseModel):
    memberIndex: int
    role: str  # "active" | "standby"
    syncStatus: str  # "in-sync" | "out-of-sync"
    priority: int
    hostname: str


class HAResponse(BaseModel):
    ha_mode: str  # "active-passive" | "active-active" | "standalone"
    members: list[HAMember] = []
    overallHealth: str  # "healthy" | "degraded" | "critical"
    message: str = ""
