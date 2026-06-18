"""
Async OpenSearch query builders for FortiGate Inbound VIP traffic analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip> (DC + DRC only)
Path filter: flow.traffic.path = "inbound-vip"
Key difference from traffic_flow: uses flow.server.l4.port.id instead of flow.application.name
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_drc_client, get_dc_client

# Index pattern for FortiGate AppID flow data
FLOW_INDEX = "fortigate-appid-flow-*"

# Site config mapping: site_name -> (source_ip, endpoint)
# Only DC and DRC for inbound VIP traffic (no Office)
SITE_FLOW_MAP: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "dc"),
    "Site_FGT-DRC": ("10.90.150.1", "drc"),
}

# Port-to-service friendly name mapping
PORT_SERVICE_MAP: dict[int, str] = {
    80: "HTTP-Browser",
    443: "HTTPS-Browser",
    22: "SSH",
    53: "DNS",
    25: "SMTP",
    21: "FTP",
    3389: "RDP",
    3306: "MySQL",
    5432: "PostgreSQL",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
}


def _port_to_service(port_value) -> str:
    """Convert port number to friendly service name."""
    try:
        port = int(port_value)
        return PORT_SERVICE_MAP.get(port, f"Port-{port}")
    except (ValueError, TypeError):
        return str(port_value)


def _get_client(site_name: str = "Site_FGT-DRC") -> AsyncOpenSearch:
    """Route to correct cluster per site."""
    _, endpoint = SITE_FLOW_MAP.get(site_name, ("", "drc"))
    if endpoint == "dc":
        return get_dc_client()
    return get_drc_client()


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    """Q-01: produce a @timestamp range filter with both gte and lte."""
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
    """Exact term filter on flow.export.ip.addr for the given site."""
    entry = SITE_FLOW_MAP.get(site_name, ("",))
    source_ip = entry[0] if entry else ""
    return {"term": {"flow.export.ip.addr": source_ip}}


def _base_filters(gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "inbound-vip", direction: str = "") -> list[dict]:
    """Q-01 + site filter + traffic path filter + optional zone-based direction filter.

    Upload:   flow.in.netif.sec.zone.name = internet  &  flow.out.netif.sec.zone.name = internal   (customer → VIP)
    Download: flow.in.netif.sec.zone.name = internal  &  flow.out.netif.sec.zone.name = internet   (VIP → customer)
    """
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter:
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    return filters


# ─────────────────────────────────────────────────────────────────
# Bytes sum aggregation helper
# ─────────────────────────────────────────────────────────────────


def _bytes_sum(name: str = "total_bytes") -> dict:
    return {name: {"sum": {"field": "flow.bytes"}}}


# ─────────────────────────────────────────────────────────────────
# TI-01: Summary — all 9 widgets in one query (Q-07: no N+1)
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC",
    path_filter: str = "inbound-vip",
) -> dict:
    """
    Returns all 9 widget data in a single OpenSearch query.
    Widgets: top_services, service_categories, top_dst_as_org, top_dst_as_country,
             top_clients, top_servers, protocol_dist, egress_breakdown,
             top_src_as_org.
    Key difference: uses flow.server.l4.port.id instead of flow.application.name.
    """
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter)}},
        "aggs": {
            # 1. Top Services (by flow.server.l4.port.id)
            "top_services": {
                "terms": {
                    "field": "flow.server.l4.port.id",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 2. Service Categories — also port-based grouping
            "service_categories": {
                "terms": {
                    "field": "flow.server.l4.port.id",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 3. Top Destination AS Organization
            "top_dst_as_org": {
                "terms": {
                    "field": "flow.dst.as.org",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 4. Top Destination AS Country
            "top_dst_as_country": {
                "terms": {
                    "field": "flow.dst.as.country",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 5. Top Client IPs
            "top_clients": {
                "terms": {
                    "field": "flow.client.ip.addr",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 6. Top Server IPs
            "top_servers": {
                "terms": {
                    "field": "flow.server.ip.addr",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 7. Protocol Distribution
            "protocol_dist": {
                "terms": {
                    "field": "l4.proto.name",
                    "size": 10,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 8. Egress Interface Breakdown
            "egress_breakdown": {
                "terms": {
                    "field": "flow.out.netif.name",
                    "size": 10,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 9. Top Source AS Organization
            "top_src_as_org": {
                "terms": {
                    "field": "flow.src.as.org",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    aggs = resp["aggregations"]

    # Helper to extract buckets
    def _buckets(agg_name: str) -> list[dict]:
        return aggs.get(agg_name, {}).get("buckets", [])

    def _total(agg_name: str) -> int:
        """Sum of all bucket values for percentage calculations."""
        return sum(
            int(b.get("total_bytes", {}).get("value", 0))
            for b in aggs.get(agg_name, {}).get("buckets", [])
        )

    # Compute grand totals for percentages
    total_service_bytes = _total("top_services") or 1
    total_proto_bytes = _total("protocol_dist") or 1

    # Duration for speed calculation
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
            {
                "org_name": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
            }
            for b in _buckets("top_dst_as_org")
        ],
        "top_dst_as_country": [
            {
                "country": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "flag_code": "",
            }
            for b in _buckets("top_dst_as_country")
        ],
        "top_clients": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {
                "ip": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "hostname": "",
            }
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
# TI-02: Stacked Bar Chart — 60s buckets with per-service breakdown
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC",
    top_n: int = 20,
    path_filter: str = "inbound-vip",
    bucket_seconds: int = 60,
) -> dict:
    """
    Returns stacked bar chart data using flow.server.l4.port.id.
    """
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval_str,
                    "extended_bounds": {
                        "min": gte_ms,
                        "max": lte_ms,
                    },
                    "min_doc_count": 0,
                },
                "aggs": {
                    "top_services": {
                        "terms": {
                            "field": "flow.server.l4.port.id",
                            "size": min(top_n, 50),  # Q-02
                            "order": {"total_bytes": "desc"},
                        },
                        "aggs": _bytes_sum(),
                    },
                    "others_bytes": {
                        "sum_bucket": {
                            "buckets_path": "top_services>total_bytes",
                        },
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
    for bucket in buckets:
        ts_ms = bucket["key"]
        from datetime import datetime, timezone
        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        row: dict = {"timestamp": ts_iso, "timestampMs": ts_ms}

        for svc_bucket in bucket["top_services"]["buckets"]:
            svc_name = _port_to_service(svc_bucket["key"])
            svc_bytes = int(svc_bucket["total_bytes"]["value"])
            row[svc_name] = svc_bytes
            all_service_bytes[svc_name] = all_service_bytes.get(svc_name, 0) + svc_bytes

        # Add Others for this bucket
        total_in_bucket = int(bucket.get("others_bytes", {}).get("value", 0) or 0)
        top_sum = sum(
            int(b["total_bytes"]["value"]) for b in bucket["top_services"]["buckets"]
        )
        others_bytes = total_in_bucket - top_sum
        if others_bytes > 0:
            row["Others"] = others_bytes
            all_service_bytes["Others"] = all_service_bytes.get("Others", 0) + others_bytes

        chart_data.append(row)

    # Sort service names by total bytes descending
    service_names = sorted(all_service_bytes, key=all_service_bytes.get, reverse=True)

    # Global speed per service (Mbps)
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
# TI-03: Sankey Diagram — dual direction: Upload (initiator) / Download (responder)
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC",
    path_filter: str = "inbound-vip",
    direction: str = "",
) -> dict:
    """Sankey for inbound VIP traffic.

    Upload (initiator):   Src AS Org → Ingress → Service → Zone
    Download (responder): Zone → Service → Egress → Dst AS Org
    """
    if client is None:
        client = _get_client(site_name)

    if direction == "download":
        # Download: Zone → Service → Egress → Dst AS Org
        sources = [
            {"zone": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"dst_as_org": {"terms": {"field": "flow.dst.as.org"}}},
        ]
        level_names = ["zone", "service", "egress", "dst_as_org"]
        level_labels = {0: "Zone", 1: "Service", 2: "Egress", 3: "Dst AS Org"}
    else:
        # Upload (initiator): Src AS Org → Ingress → Service → Zone
        sources = [
            {"src_as_org": {"terms": {"field": "flow.src.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"zone": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["src_as_org", "ingress", "service", "zone"]
        level_labels = {0: "Src AS Org", 1: "Ingress", 2: "Service", 3: "Zone"}

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, direction)}},
        "aggs": {
            "sankey_flow": {
                "composite": {
                    "size": 1000,
                    "sources": sources,
                },
                "aggs": _bytes_sum(),
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["sankey_flow"]["buckets"]

    # Collect raw rows; port-0 is meaningless for inbound services
    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        # Filter out port-0 for service field regardless of direction
        port_key = key.get("service", "")
        if port_key == "0" or port_key == 0:
            continue
        bytes_val = int(bucket["total_bytes"]["value"])
        if bytes_val == 0:
            continue
        row = {}
        for i, name in enumerate(level_names):
            raw = key.get(name, "Unknown")
            if name == "service":
                row[name] = _port_to_service(raw)
            else:
                row[name] = raw
        row["bytes"] = bytes_val
        rows.append(row)

    if not rows:
        return {"nodes": [], "links": []}

    # Compute total bytes per label at each level
    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    level_totals = {name: _level_totals(rows, name) for name in level_names}

    # Top 10 per level
    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:n]}

    top_sets = {name: _top_n(level_totals[name]) for name in level_names}

    # Filter rows to only top-N values at each level
    filtered_rows = [
        r for r in rows
        if all(r[name] in top_sets[name] for name in level_names)
    ]

    # Build node map: (level, label) → id
    nodes_list: list[dict] = []
    node_index: dict[tuple[int, str], int] = {}

    def _get_node_id(level: int, label: str) -> int:
        key = (level, label)
        if key not in node_index:
            idx = len(nodes_list)
            node_index[key] = idx
            nodes_list.append({"id": idx, "label": label, "level": level})
        return node_index[key]

    # Links aggregation
    link_map: dict[tuple[int, int], int] = defaultdict(int)

    for r in filtered_rows:
        ids = [_get_node_id(i, r[name]) for i, name in enumerate(level_names)]
        for i in range(len(ids) - 1):
            link_map[(ids[i], ids[i + 1])] += r["bytes"]

    links_list = [
        {"source": src, "target": tgt, "value": val}
        for (src, tgt), val in link_map.items()
    ]

    return {"nodes": nodes_list, "links": links_list}


# ─────────────────────────────────────────────────────────────────
# TI-04: Data Table — composite aggregation with pagination
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC",
    after: Optional[dict] = None,
    page_size: int = 100,
    path_filter: str = "inbound-vip",
) -> dict:
    """
    Returns paginated flow records using composite aggregation.
    Keys: client_ip, server_ip, service_port.
    """
    if client is None:
        client = _get_client(site_name)

    # Q-08: cap page size
    capped_size = min(page_size, 500)

    composite_sources = [
        {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
        {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
        {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
    ]

    composite_body: dict = {
        "size": capped_size,
        "sources": composite_sources,
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter)}},
        "aggs": {
            "flow_table": {
                "composite": composite_body,
                "aggs": {
                    "total_bytes": {"sum": {"field": "flow.bytes"}},
                    "total_packets": {"sum": {"field": "flow.packets"}},
                    "session_count": {
                        "cardinality": {"field": "flow.connection_id"}
                    },
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

    after_key = result.get("after_key", None)

    return {
        "records": records,
        "after_key": after_key,
    }
