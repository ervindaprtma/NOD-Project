"""
Async OpenSearch query builders for FortiGate AppID traffic flow analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip>
Routes: DC→telegraf cluster, DRC+Office→appid cluster
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_drc_client, get_dc_client

# Index pattern for FortiGate AppID flow data
FLOW_INDEX = "fortigate-appid-flow-*"

# Site config mapping: site_name -> (source_ip, endpoint)
SITE_FLOW_MAP: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "dc"),
    "Site_FGT-DRC": ("10.90.150.1", "drc"),
    "Site_FGT_Office": ("10.10.10.10", "drc"),
}


def _get_client(site_name: str = "Site_FGT_Office") -> AsyncOpenSearch:
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


def _base_filters(gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "internet", direction: str = "") -> list[dict]:
    """Q-01 + site filter + traffic path filter + optional zone-based direction filter.

    Upload:   flow.in.netif.sec.zone.name = internal  &  flow.out.netif.sec.zone.name = internet
    Download: flow.in.netif.sec.zone.name = internet  &  flow.out.netif.sec.zone.name = internal
    """
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter:
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    return filters


# ─────────────────────────────────────────────────────────────────
# Bytes sum aggregation helper
# ─────────────────────────────────────────────────────────────────


def _bytes_sum(name: str = "total_bytes") -> dict:
    return {name: {"sum": {"field": "flow.bytes"}}}


# ─────────────────────────────────────────────────────────────────
# TF-01: Summary — all 8 widgets in one query (Q-07: no N+1)
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    path_filter: str = "internet",
) -> dict:
    """
    Returns all 8 widget data in a single OpenSearch query.
    Widgets: top_apps, app_categories, top_dst_as_org, top_dst_as_country,
             top_clients, top_servers, protocol_dist, egress_breakdown,
             top_src_as_org.
    """
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter)}},
        "aggs": {
            # 1. Top Applications (by flow.application.name)
            "top_apps": {
                "terms": {
                    "field": "flow.application.name",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": _bytes_sum(),
            },
            # 2. Application Categories — derive from L4 port name grouping
            #    No explicit category field exists; we bucket by prefix for grouping.
            #    Using the same port.name terms as categories.
            "app_categories": {
                "terms": {
                    "field": "flow.application.name",
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
    total_app_bytes = _total("top_apps") or 1
    total_proto_bytes = _total("protocol_dist") or 1

    # Duration for speed calculation
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    return {
        "top_apps": [
            {
                "app_name": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
                "percentage": round(int(b["total_bytes"]["value"]) / total_app_bytes * 100, 2),
            }
            for b in _buckets("top_apps")
        ],
        "app_categories": [
            {
                "category_name": b["key"],
                "total_bytes": int(b["total_bytes"]["value"]),
                "count": b["doc_count"],
            }
            for b in _buckets("app_categories")
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
                "flag_code": "",  # Frontend maps country -> flag
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
# TF-02: Stacked Bar Chart — 60s buckets with per-app breakdown
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    top_n: int = 20,
    path_filter: str = "internet",
    bucket_seconds: int = 60,
) -> dict:
    """
    Returns stacked bar chart data.
    - bucket_seconds: dynamic interval based on time range
    - Top N apps per bucket by flow.bytes
    - Global speed per app in Mbps
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
                    "top_apps": {
                        "terms": {
                            "field": "flow.application.name",
                            "size": min(top_n, 50),  # Q-02
                            "order": {"total_bytes": "desc"},
                        },
                        "aggs": _bytes_sum(),
                    },
                    "others_bytes": {
                        "sum_bucket": {
                            "buckets_path": "top_apps>total_bytes",
                        },
                    },
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["per_minute"]["buckets"]

    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)
    all_app_bytes: dict[str, int] = {}

    chart_data: list[dict] = []
    for bucket in buckets:
        ts_ms = bucket["key"]
        # ISO timestamp from epoch millis
        from datetime import datetime, timezone
        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        row: dict = {"timestamp": ts_iso, "timestampMs": ts_ms}

        for app_bucket in bucket["top_apps"]["buckets"]:
            app_name = app_bucket["key"]
            app_bytes = int(app_bucket["total_bytes"]["value"])
            row[app_name] = app_bytes
            all_app_bytes[app_name] = all_app_bytes.get(app_name, 0) + app_bytes

        # Add Others for this bucket
        total_in_bucket = int(bucket.get("others_bytes", {}).get("value", 0) or 0)
        top_sum = sum(
            int(b["total_bytes"]["value"]) for b in bucket["top_apps"]["buckets"]
        )
        others_bytes = total_in_bucket - top_sum
        if others_bytes > 0:
            row["Others"] = others_bytes
            all_app_bytes["Others"] = all_app_bytes.get("Others", 0) + others_bytes

        chart_data.append(row)

    # Sort app names by total bytes descending
    app_names = sorted(all_app_bytes, key=all_app_bytes.get, reverse=True)

    # Global speed per app (Mbps)
    global_speed_by_app: dict[str, float] = {
        app: (total_bytes * 8) / duration_s / 1_000_000
        for app, total_bytes in all_app_bytes.items()
    }

    return {
        "chart_data": chart_data,
        "app_names": app_names,
        "global_speed_by_app": global_speed_by_app,
    }


# ─────────────────────────────────────────────────────────────────
# TF-03: Sankey Diagram — multi-level flow using composite aggregation
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    path_filter: str = "internet",
    direction: str = "",
) -> dict:
    """Build Sankey nodes+links for Internet traffic flow.

    Upload (initiator):  Zone → Apps → Egress → Dst AS Organization
    Download (responder): Src AS Org → Ingress → Apps → Zone

    Uses composite aggregation. Top 10 per level by total bytes.
    """
    if client is None:
        client = _get_client(site_name)

    if direction == "download":
        # Download: Src AS Org → Ingress → Apps → Zone
        sources = [
            {"src_as": {"terms": {"field": "flow.src.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"zone": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["src_as", "ingress", "app", "zone"]
        level_labels = {0: "Src AS Org", 1: "Ingress", 2: "App", 3: "Zone"}
    else:
        # Upload (initiator): Zone → Apps → Egress → Dst AS Org
        sources = [
            {"zone": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"as_org": {"terms": {"field": "flow.dst.as.org"}}},
        ]
        level_names = ["zone", "app", "egress", "as_org"]
        level_labels = {0: "Zone", 1: "App", 2: "Egress", 3: "Dst AS Org"}

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

    # Collect raw rows, filtering out app-0
    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        app_name = key.get("app", "")
        if app_name == "app-0":
            continue
        bytes_val = int(bucket["total_bytes"]["value"])
        if bytes_val == 0:
            continue
        row = {}
        for i, name in enumerate(level_names):
            row[name] = key.get(name, "Unknown")
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

    # Links aggregation: (source_node_id, target_node_id) → bytes sum
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
# TF-04: Data Table — composite aggregation with pagination
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    after: Optional[dict] = None,
    page_size: int = 100,
    path_filter: str = "internet",
) -> dict:
    """
    Returns paginated flow records using composite aggregation.
    Keys: client_ip, server_ip, app_name.
    Sub-aggs: total_bytes (sum flow.bytes), total_packets (sum flow.packets),
              session_count (cardinality on flow.connection_id).
    """
    if client is None:
        client = _get_client(site_name)

    # Q-08: cap page size
    capped_size = min(page_size, 500)

    composite_sources = [
        {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
        {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
        {"app_name": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
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
        records.append({
            "client_ip": key.get("client_ip") or "",
            "server_ip": key.get("server_ip") or "",
            "app_name": key.get("app_name") or "Unknown",
            "bytes": int(bucket.get("total_bytes", {}).get("value", 0)),
            "packets": int(bucket.get("total_packets", {}).get("value", 0)),
            "sessions": int(bucket.get("session_count", {}).get("value", 0)),
        })

    after_key = result.get("after_key", None)

    return {
        "records": records,
        "after_key": after_key,
    }
