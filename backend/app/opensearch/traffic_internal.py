"""
Async OpenSearch query builders for Internal Traffic analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip> (all sites: DC, DRC, Office)
Path filter: flow.traffic.path = "intra-lan" OR "inter-site"
Key dimension: flow.server.l4.port.id (service/port-based, like traffic_inbound)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_drc_client, get_dc_client

FLOW_INDEX = "fortigate-appid-flow-*"

# All three sites for internal traffic
SITE_FLOW_MAP: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "dc"),
    "Site_FGT-DRC": ("10.90.150.1", "drc"),
    "Site_FGT_Office": ("10.10.10.10", "drc"),
}

# Comprehensive port-to-service friendly name mapping
PORT_SERVICE_MAP: dict[int, str] = {
    20: "FTP-Data",
    21: "FTP-Control",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    67: "DHCP-Server",
    68: "DHCP-Client",
    69: "TFTP",
    80: "HTTP-Browser",
    110: "POP3",
    123: "NTP",
    143: "IMAP",
    161: "SNMP",
    389: "LDAP",
    443: "HTTPS-Browser",
    445: "SMB",
    465: "SMTPS",
    514: "Syslog",
    587: "SMTP-Submit",
    636: "LDAPS",
    993: "IMAPS",
    995: "POP3S",
    1433: "MSSQL",
    1521: "Oracle",
    3306: "MySQL",
    3389: "RDP-Access",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
    9090: "Prometheus",
    9200: "Elasticsearch",
    27017: "MongoDB",
}


def _port_to_service(port_value) -> str:
    """Convert port number to friendly service name."""
    try:
        port = int(port_value)
        return PORT_SERVICE_MAP.get(port, f"Port-{port}")
    except (ValueError, TypeError):
        return str(port_value)


def _get_client(site_name: str = "Site_FGT_Office") -> AsyncOpenSearch:
    """Route to correct cluster per site."""
    _, endpoint = SITE_FLOW_MAP.get(site_name, ("", "drc"))
    if endpoint == "dc":
        return get_dc_client()
    return get_drc_client()


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    """Q-01: @timestamp range filter with both gte and lte."""
    return {
        "range": {
            "@timestamp": {
                "gte": gte_ms,
                "lte": lte_ms,
                "format": "epoch_millis",
            }
        }
    }


def _site_filter(site_name: str) -> dict:
    """Exact term filter on flow.export.ip.addr."""
    entry = SITE_FLOW_MAP.get(site_name, ("",))
    source_ip = entry[0] if entry else ""
    return {"term": {"flow.export.ip.addr": source_ip}}


def _internal_path_filter() -> dict:
    """Match either intra-lan OR inter-site traffic paths."""
    return {
        "bool": {
            "should": [
                {"term": {"flow.traffic.path": "intra-lan"}},
                {"term": {"flow.traffic.path": "inter-site"}},
            ],
            "minimum_should_match": 1,
        }
    }


def _base_filters(gte_ms: int, lte_ms: int, site_name: str) -> list[dict]:
    """Q-01 + site filter + internal path filter (intra-lan OR inter-site)."""
    return [
        _time_range(gte_ms, lte_ms),
        _site_filter(site_name),
        _internal_path_filter(),
    ]


# ─────────────────────────────────────────────────────────────────
# Bytes sum helper
# ─────────────────────────────────────────────────────────────────


def _bytes_sum(name: str = "total_bytes") -> dict:
    return {name: {"sum": {"field": "flow.bytes"}}}


# ─────────────────────────────────────────────────────────────────
# TI-01: Summary — 9 widgets, port/service-based
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
) -> dict:
    """Returns all 9 widget data. Uses flow.server.l4.port.id for service dimension."""
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "top_services": {
                "terms": {
                    "field": "flow.server.l4.port.id",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "service_categories": {
                "terms": {
                    "field": "flow.server.l4.port.id",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "top_dst_as_org": {
                "terms": {
                    "field": "flow.dst.as.org",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "top_dst_as_country": {
                "terms": {
                    "field": "flow.dst.as.country",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "top_clients": {
                "terms": {
                    "field": "flow.client.ip.addr",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {
                    "field": "flow.server.ip.addr",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "protocol_dist": {
                "terms": {
                    "field": "l4.proto.name",
                    "size": 10,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "egress_breakdown": {
                "terms": {
                    "field": "flow.out.netif.name",
                    "size": 10,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            "top_src_as_org": {
                "terms": {
                    "field": "flow.src.as.org",
                    "size": 20,
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict]:
        return aggs.get(agg_name, {}).get("buckets", [])

    def _total(agg_name: str) -> int:
        return sum(
            int(b.get("total_bytes", {}).get("value", 0))
            for b in aggs.get(agg_name, {}).get("buckets", [])
        )

    total_service_bytes = _total("top_services") or 1
    total_proto_bytes = _total("protocol_dist") or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    return {
        "top_services": [
            {
                "service_name": _port_to_service(b["key"]),
                "service_port": int(b["key"]) if str(b["key"]).isdigit() else b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
                "percentage": round(int(b["total_bytes"]["value"]) / total_service_bytes * 100, 2),
            }
            for b in _buckets("top_services")
        ],
        "service_categories": [
            {
                "category_name": _port_to_service(b["key"]),
                "total_bytes": int(b["total_bytes"]["value"]),
                "count": b["doc_count"],
            }
            for b in _buckets("service_categories")
        ],
        "top_dst_as_org": [
            {"org_name": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_dst_as_org")
        ],
        "top_dst_as_country": [
            {"country": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "flag_code": ""}
            for b in _buckets("top_dst_as_country")
        ],
        "top_clients": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "hostname": ""}
            for b in _buckets("top_servers")
        ],
        "protocol_dist": [
            {
                "protocol": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "percentage": round(int(b["total_bytes"]["value"]) / total_proto_bytes * 100, 2),
            }
            for b in _buckets("protocol_dist")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
        "top_src_as_org": [
            {"org_name": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_src_as_org")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TI-02: Stacked Bar Chart — service-based
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
    top_n: int = 20,
    bucket_seconds: int = 60,
) -> dict:
    """Stacked bar chart using flow.server.l4.port.id."""
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval_str,
                    "extended_bounds": {"min": gte_ms, "max": lte_ms},
                    "min_doc_count": 0,
                },
                "aggs": {
                    "top_services": {
                        "terms": {
                            "field": "flow.server.l4.port.id",
                            "size": min(top_n, 50),
                            "order": {"total_bytes": "desc"},
                        },
                        "aggs": _bytes_sum(),
                    },
                    "others_bytes": {
                        "sum_bucket": {"buckets_path": "top_services>total_bytes"},
                    },
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["per_minute"]["buckets"]

    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)
    all_service_bytes: dict[str, int] = {}

    chart_data: list[dict] = []
    from datetime import datetime, timezone

    for bucket in buckets:
        ts_ms = bucket["key"]
        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        row: dict = {"timestamp": ts_iso, "timestampMs": ts_ms}

        for svc_bucket in bucket["top_services"]["buckets"]:
            svc_name = _port_to_service(svc_bucket["key"])
            svc_bytes = int(svc_bucket["total_bytes"]["value"])
            row[svc_name] = svc_bytes
            all_service_bytes[svc_name] = all_service_bytes.get(svc_name, 0) + svc_bytes

        total_in_bucket = int(bucket.get("others_bytes", {}).get("value", 0) or 0)
        top_sum = sum(
            int(b["total_bytes"]["value"]) for b in bucket["top_services"]["buckets"]
        )
        others_bytes = total_in_bucket - top_sum
        if others_bytes > 0:
            row["Others"] = others_bytes
            all_service_bytes["Others"] = all_service_bytes.get("Others", 0) + others_bytes

        chart_data.append(row)

    service_names = sorted(all_service_bytes, key=all_service_bytes.get, reverse=True)

    global_speed_by_service: dict[str, float] = {
        svc: (total_bytes * 8) / duration_s / 1_000_000
        for svc, total_bytes in all_service_bytes.items()
    }

    return {
        "chart_data": chart_data,
        "service_names": service_names,
        "global_speed_by_service": global_speed_by_service,
    }


# ─────────────────────────────────────────────────────────────────
# TI-03: Sankey — Zone → Service → Egress → AS Org
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
) -> dict:
    """Sankey: Ingress → Service → Egress (3 levels)."""
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "sankey_flow": {
                "composite": {
                    "size": 1000,
                    "sources": [
                        {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
                        {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
                        {"egress": {"terms": {"field": "flow.out.netif.name"}}},
                    ],
                },
                "aggs": _bytes_sum(),
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["sankey_flow"]["buckets"]

    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        port_key = key.get("service", "")
        if port_key == "0" or port_key == 0:
            continue
        bytes_val = int(bucket["total_bytes"]["value"])
        if bytes_val == 0:
            continue
        rows.append({
            "ingress": key.get("ingress", "Unknown"),
            "service": _port_to_service(port_key),
            "egress": key.get("egress", "Unknown"),
            "bytes": bytes_val,
        })

    if not rows:
        return {"nodes": [], "links": []}

    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    ingress_totals = _level_totals(rows, "ingress")
    svc_totals = _level_totals(rows, "service")
    egress_totals = _level_totals(rows, "egress")

    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:n]}

    top_ingress = _top_n(ingress_totals)
    top_services = _top_n(svc_totals)
    top_egress = _top_n(egress_totals)

    filtered_rows = [
        r for r in rows
        if r["ingress"] in top_ingress
        and r["service"] in top_services
        and r["egress"] in top_egress
    ]

    nodes_list: list[dict] = []
    node_index: dict[tuple[int, str], int] = {}

    def _get_node_id(level: int, label: str) -> int:
        key = (level, label)
        if key not in node_index:
            idx = len(nodes_list)
            node_index[key] = idx
            nodes_list.append({"id": idx, "label": label, "level": level})
        return node_index[key]

    # Links: ingress → service → egress
    link_map: dict[tuple[int, int], int] = defaultdict(int)

    for r in filtered_rows:
        ingress_id = _get_node_id(0, r["ingress"])
        svc_id = _get_node_id(1, r["service"])
        egress_id = _get_node_id(2, r["egress"])
        link_map[(ingress_id, svc_id)] += r["bytes"]
        link_map[(svc_id, egress_id)] += r["bytes"]

    links_list = [
        {"source": src, "target": tgt, "value": val}
        for (src, tgt), val in link_map.items()
    ]

    return {"nodes": nodes_list, "links": links_list}


# ─────────────────────────────────────────────────────────────────
# TI-04: Data Table — composite with service_port key
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
    after: Optional[dict] = None,
    page_size: int = 100,
) -> dict:
    """Paginated flow records with service_port dimension."""
    if client is None:
        client = _get_client(site_name)

    capped_size = min(page_size, 500)

    composite_sources = [
        {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
        {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
        {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
    ]

    composite_body: dict = {"size": capped_size, "sources": composite_sources}
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "flow_table": {
                "composite": composite_body,
                "aggs": {
                    "total_bytes": {"sum": {"field": "flow.bytes"}},
                    "total_packets": {"sum": {"field": "flow.packets"}},
                    "session_count": {"cardinality": {"field": "flow.connection_id"}},
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    result = resp["aggregations"]["flow_table"]

    records = []
    for bucket in result["buckets"]:
        key = bucket["key"]
        port_raw = key.get("service_port") or ""
        records.append({
            "client_ip": key.get("client_ip") or "",
            "server_ip": key.get("server_ip") or "",
            "service_name": _port_to_service(port_raw),
            "service_port": int(port_raw) if str(port_raw).isdigit() else port_raw,
            "bytes": int(bucket.get("total_bytes", {}).get("value", 0)),
            "packets": int(bucket.get("total_packets", {}).get("value", 0)),
            "sessions": int(bucket.get("session_count", {}).get("value", 0)),
        })

    return {"records": records, "after_key": result.get("after_key", None)}
