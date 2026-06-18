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


class EgressBreakdownItem(BaseModel):
    interface: str
    total_bytes: int


# ── SUMMARY RESPONSE ────────────────────────────────────────────


class TrafficInboundSummaryResponse(BaseModel):
    top_services: list[TopServiceItem]
    top_src_as_org: list[TopASOrgItem]
    top_src_as_country: list[TopASCountryItem]
    top_clients: list[TopClientItem]
    top_servers: list[TopServerItem]
    protocol_dist: list[ProtocolDistItem]
    egress_breakdown: list[EgressBreakdownItem]


# ── CHART RESPONSE ───────────────────────────────────────────────


class TrafficInboundChartResponse(BaseModel):
    chart_data: list[dict]
    service_names: list[str]


# ── TABLE RESPONSE ───────────────────────────────────────────────


class InboundFlowTableRecord(BaseModel):
    client_ip: str
    server_ip: str
    service: str = "Unknown"
    bytes: int = 0
    packets: int = 0
    sessions: int = 0


class TrafficInboundTableResponse(BaseModel):
    records: list[InboundFlowTableRecord]
    after_key: Optional[dict] = None
    total: int = 0


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
    as_country_nodes: list[dict] = []
    as_country_links: list[dict] = []
