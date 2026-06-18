"""
Traffic Inbound schemas for VIP inbound traffic analytics.
Uses flow.server.l4.port.id for service-level analysis.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── WIDGET ITEMS ─────────────────────────────────────────────────


class TopServiceItem(BaseModel):
    service_name: str
    service_port: int | str = 0
    total_bytes: int
    speed_mbps: float
    percentage: float


class ServiceCategoryItem(BaseModel):
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


class TopServerItem(BaseModel):
    ip: str
    total_bytes: int
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


class TrafficInboundSummaryResponse(BaseModel):
    top_services: list[TopServiceItem]
    service_categories: list[ServiceCategoryItem]
    top_dst_as_org: list[TopASOrgItem]
    top_dst_as_country: list[TopASCountryItem]
    top_clients: list[TopClientItem]
    top_servers: list[TopServerItem]
    protocol_dist: list[ProtocolDistItem]
    egress_breakdown: list[EgressBreakdownItem]
    top_src_as_org: list[TopSrcASOrgItem]


# ── CHART RESPONSE ───────────────────────────────────────────────


class TrafficInboundChartResponse(BaseModel):
    chart_data: list[dict]
    service_names: list[str]
    global_speed_by_service: dict[str, float]


# ── TABLE RESPONSE ───────────────────────────────────────────────


class InboundFlowTableRecord(BaseModel):
    client_ip: str
    server_ip: str
    service_name: str = "Unknown"
    service_port: int | str = 0
    bytes: int = 0
    packets: int = 0
    sessions: int = 0


class TrafficInboundTableResponse(BaseModel):
    records: list[InboundFlowTableRecord]
    after_key: Optional[dict] = None


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
