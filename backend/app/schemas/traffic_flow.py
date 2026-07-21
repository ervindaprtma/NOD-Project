"""
Traffic Flow schemas for the ElastiFlow-based traffic analytics module.
Uses elastiflow-flow-codex-2.5-rollover-* index field mappings.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── SITE CONFIGURATION ───────────────────────────────────────────

# Per-site source IP filter (flow.export.ip.addr) + endpoint routing
SITE_FLOW_CONFIG: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "telegraf"),
    "Site_FGT-DRC": ("10.90.150.1", "appid"),
    "Site_FGT_Office": ("10.10.10.10", "appid"),
}

# ── WIDGET ITEMS ─────────────────────────────────────────────────


class TopAppItem(BaseModel):
    app_name: str
    total_bytes: int
    speed_mbps: float
    percentage: float


class AppCategoryItem(BaseModel):
    category_name: str
    total_bytes: int
    count: int


class TopASOrgItem(BaseModel):
    org_name: str
    total_bytes: int


class TopASCountryItem(BaseModel):
    country: str
    total_bytes: int
    flag_code: str = ""


class TopClientItem(BaseModel):
    ip: str
    total_bytes: int
    upload_bytes: int = 0
    download_bytes: int = 0


class TopServerItem(BaseModel):
    ip: str
    total_bytes: int
    upload_bytes: int = 0
    download_bytes: int = 0
    hostname: str = ""


class ProtocolDistItem(BaseModel):
    protocol: str
    total_bytes: int
    percentage: float


class EgressBreakdownItem(BaseModel):
    interface: str
    total_bytes: int


class TopSrcASOrgItem(BaseModel):
    org_name: str
    total_bytes: int


# ── SUMMARY RESPONSE ────────────────────────────────────────────


class TrafficSummaryResponse(BaseModel):
    total_bytes: int = 0
    total_upload: int = 0
    total_download: int = 0
    total_sessions: int = 0
    top_apps: list[TopAppItem]
    app_categories: list[AppCategoryItem]
    top_dst_as_org: list[TopASOrgItem]
    top_dst_as_country: list[TopASCountryItem]
    top_clients: list[TopClientItem]
    top_servers: list[TopServerItem]
    top_src_as_org: list[TopSrcASOrgItem] = []
    protocol_dist: list[ProtocolDistItem]
    egress_breakdown: list[EgressBreakdownItem]
    ingress_breakdown: list[EgressBreakdownItem]


# ── CHART RESPONSE ───────────────────────────────────────────────


class TrafficChartResponse(BaseModel):
    chart_data: list[dict]  # [{timestamp, timestampMs, app1: bytes, app2: bytes, ...}]
    app_names: list[str]
    bucket_seconds: int = 60  # actual bucket width used; frontend divides bytes by this for Mbps
    global_speed_by_app: dict[str, float] = {}


# ── TABLE RESPONSE ───────────────────────────────────────────────


class FlowTableRecord(BaseModel):
    client_ip: str
    server_ip: str
    app_name: str = "Unknown"
    bytes: int = 0
    upload_bytes: int = 0
    download_bytes: int = 0
    packets: int = 0
    sessions: int = 0


class TrafficTableResponse(BaseModel):
    records: list[FlowTableRecord]
    after_key: Optional[dict] = None


# ── RAW FLOW RECORD ─────────────────────────────────────────────


class RawFlowRecord(BaseModel):
    timestamp: str  # ISO 8601
    client_ip: str
    server_ip: str
    application: str
    category: str
    protocol: str
    dst_port: int
    total_bytes: int
    bytes_human: Optional[str] = None
    packets: int
    ingress_interface: str
    egress_interface: str
    classification: str = "unresolved"  # dpi_verified | port_inferred | port_suspicious | unresolved
    path: str  # traffic path: internet, inbound-vip, inter-site, intra-lan
    correlation_id: Optional[str] = None
    correlation_direction: Optional[str] = None


# ── SANKEY RESPONSE ──────────────────────────────────────────────


class SankeyNode(BaseModel):
    id: int
    label: str
    level: int


class SankeyLink(BaseModel):
    source: int
    target: int
    value: int


class SankeyResponse(BaseModel):
    nodes: list[SankeyNode]
    links: list[SankeyLink]