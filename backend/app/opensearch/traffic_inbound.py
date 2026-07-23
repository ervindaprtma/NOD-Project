"""
Async OpenSearch query builders for FortiGate AppID traffic inbound (VIP).
Index: fortigate-appid-flow-*
Key difference: uses flow.server.l4.port.id (service/port-based).
Routes: DC→dc cluster, DRC→drc cluster.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch._common import (
    FLOW_INDEX, _time_range, _multi_term, _multi_term_any, _multi_wildcard, _bytes_sum, _port_to_service, BYTES_DESC,
)
from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search, drop_partial_tail, spread_long_sessions

SITE_SOURCE_IPS: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
}


def _get_client(site_name: str = "Site_FGT-DRC") -> AsyncOpenSearch:
    if site_name == "Site_FGT-DRC":
        return get_drc_client()
    return get_dc_client()


def _site_filter(site_name: str) -> dict:
    source_ip = SITE_SOURCE_IPS.get(site_name, "")
    return {"term": {"flow.export.ip.addr": source_ip}}


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "inbound-vip",
    direction: str = "", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None, src_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter and path_filter != "all":
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    f = _multi_wildcard("flow.application.name", app_filter)
    if f: filters.append(f)
    f = _multi_term("flow.client.ip.addr", client_ip)
    if f: filters.append(f)
    f = _multi_term("flow.server.ip.addr", server_ip)
    if f: filters.append(f)
    f = _multi_term("l4.proto.name", protocol.upper())  # proto names are uppercase (TCP/UDP/ICMP); accept any-case input
    if f: filters.append(f)
    if dst_port is not None:
        # Role-based (stable across both legs) — see traffic_flow._base_filters.
        filters.append({"term": {"flow.server.l4.port.id": dst_port}})
    f = _multi_wildcard("flow.client.as.org", src_as_org)
    if f: filters.append(f)
    f = _multi_term_any(['flow.in.netif.name', 'flow.in.netif.alias'], ingress_interface)
    if f: filters.append(f)
    f = _multi_term_any(['flow.out.netif.name', 'flow.out.netif.alias'], egress_interface)
    if f: filters.append(f)
    return filters


# ─────────────────────────────────────────────────────────────────
# TI-01: Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", path_filter: str = "inbound-vip",
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None, src_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, src_as_org=src_as_org, ingress_interface=ingress_interface, egress_interface=egress_interface)}},
        "aggs": {
            "grand_total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "grand_total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
            "session_count": {"cardinality": {"field": "flow.connection_id"}},
            "top_services": {
                "terms": {"field": "flow.server.l4.port.id", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            # On the `inbound-vip` path the EXTERNAL requester is the client on BOTH
            # legs. server.as.org here resolves to our own published VIP's upstream
            # AS (our ISP), not the requester — so both legs aggregate client.as.org.
            "top_as_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.client.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_as_download": {
                "filter": {"term": {"flow.out.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.client.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.src.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_download": {
                "filter": {"term": {"flow.out.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.dst.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_clients": {
                "terms": {"field": "flow.client.ip.addr", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {"field": "flow.server.ip.addr", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "protocol_dist": {
                "terms": {"field": "l4.proto.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "ingress_breakdown": {
                "terms": {"field": "flow.in.netif.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "egress_breakdown": {
                "terms": {"field": "flow.out.netif.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict]:
        return list(aggs.get(agg_name, {}).get("buckets", []))

    def _merge_filter_buckets(upload_agg: str, download_agg: str, inner_agg: str) -> list[dict]:
        merged: dict[str, int] = {}
        for agg_name in (upload_agg, download_agg):
            filter_result = aggs.get(agg_name, {})
            for b in filter_result.get(inner_agg, {}).get("buckets", []):
                key = b["key"]
                merged[key] = merged.get(key, 0) + int(b["total_bytes"]["value"])
        return [{"key": k, "total_bytes": v} for k, v in sorted(merged.items(), key=lambda x: -x[1])[:20]]

    as_client_buckets = _merge_filter_buckets("top_as_upload", "top_as_download", "as_orgs")
    country_client_buckets = _merge_filter_buckets("top_country_upload", "top_country_download", "countries")

    total_service_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("top_services")) or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    grand_upload = int(aggs.get("grand_total_upload", {}).get("value", 0))
    grand_download = int(aggs.get("grand_total_download", {}).get("value", 0))

    return {
        "total_bytes": grand_upload + grand_download,
        "total_upload": grand_upload,
        "total_download": grand_download,
        "total_sessions": int(aggs.get("session_count", {}).get("value", 0)),
        "top_services": [
            {"service_name": _port_to_service(b["key"]), "service_port": int(b["key"]) if str(b["key"]).isdigit() else b["key"],
             "total_bytes": int(b["total_bytes"]["value"]),
             "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
             "percentage": round(int(b["total_bytes"]["value"]) / total_service_bytes * 100, 2)}
            for b in _buckets("top_services")
        ],
        "top_src_as_org": [
            {"org_name": b["key"], "total_bytes": b["total_bytes"]}
            for b in as_client_buckets
        ],
        "top_src_as_country": [
            {"country": b["key"], "total_bytes": b["total_bytes"], "flag_code": ""}
            for b in country_client_buckets
        ],
        "top_clients": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "upload_bytes": int(b["upload_bytes"]["value"]),
             "download_bytes": int(b["download_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "upload_bytes": int(b["upload_bytes"]["value"]),
             "download_bytes": int(b["download_bytes"]["value"]), "hostname": ""}
            for b in _buckets("top_servers")
        ],
        "protocol_dist": [
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("protocol_dist")
        ],
        "ingress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("ingress_breakdown")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TI-02: Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", top_n: int = 20, path_filter: str = "inbound-vip",
    bucket_seconds: int = 60, app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None, src_as_org: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    base_filter = _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, src_as_org=src_as_org)
    body = {
        "size": 0,
        "query": {"bool": {"filter": base_filter}},
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str},
                "aggs": {
                    "top_services": {
                        "terms": {"field": "flow.server.l4.port.id", "size": top_n, "order": BYTES_DESC},
                        "aggs": _bytes_sum(),
                    }
                },
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    result = resp["aggregations"]["per_minute"]

    # Base: per-bucket per-service bytes by @timestamp.
    bucket_svc: dict[int, dict[str, float]] = {}
    for bucket in drop_partial_tail(result["buckets"], bucket_seconds, lte_ms):
        svc_map: dict[str, float] = {}
        for svc_bucket in bucket["top_services"]["buckets"]:
            svc_bytes = int(svc_bucket["total_bytes"]["value"])
            if svc_bytes <= 0:
                continue
            svc_name = _port_to_service(svc_bucket["key"])
            svc_map[svc_name] = svc_map.get(svc_name, 0.0) + float(svc_bytes)
        bucket_svc[int(bucket["key"])] = svc_map

    # Session-close-spike fix: re-spread long sessions (keyed by service port). No global
    # top-port list here (per-bucket top-N), so the fetch is byte-bounded + name-gated.
    charted = {svc for m in bucket_svc.values() for svc in m}
    await spread_long_sessions(
        client, base_filter, "flow.server.l4.port.id", _port_to_service, charted,
        bucket_svc, bucket_seconds, gte_ms, lte_ms,
    )

    service_set: set[str] = set()
    chart_data: list[dict[str, Any]] = []
    for ts_ms in sorted(bucket_svc):
        row: dict[str, Any] = {"timestamp": ts_ms, "timestampMs": ts_ms}
        for svc_name, sb in bucket_svc[ts_ms].items():
            if sb <= 0:
                continue
            row[svc_name] = int(round(sb))
            service_set.add(svc_name)
        chart_data.append(row)

    return {"chart_data": chart_data, "service_names": sorted(service_set), "bucket_seconds": bucket_seconds}


# ─────────────────────────────────────────────────────────────────
# TI-03: Sankey — direction-aware pola
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", path_filter: str = "inbound-vip",
    direction: str = "", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None, src_as_org: str = "",
) -> dict:
    """Sankey for inbound VIP traffic.

    Upload (customer->VIP):  AS Org -> Ingress -> Service -> Egress
    Download (VIP->customer): Ingress -> Service -> Egress -> AS Org
    Node order is per-direction (cleaner left-to-right view); Upload weights links
    by flow.client.bytes, Download by flow.server.bytes, empty direction by their
    sum. No zone filter — path_filter is the separate selector.
    """
    if client is None:
        client = _get_client(site_name)

    # Per-direction node order only. direction is NOT passed to _base_filters (no zone
    # filter); path_filter stays the separate selector. AS Org = flow.client.as.org
    # (the remote customer is the client on an inbound-vip flow).
    if direction == "download":
        # Download (VIP->customer): Ingress -> Service -> Egress -> AS Org
        sources = [
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"as_org": {"terms": {"field": "flow.client.as.org"}}},
        ]
        level_names = ["ingress", "service", "egress", "as_org"]
    else:
        # Upload (customer->VIP) and empty: AS Org -> Ingress -> Service -> Egress
        sources = [
            {"as_org": {"terms": {"field": "flow.client.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["as_org", "ingress", "service", "egress"]

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, "", app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, src_as_org=src_as_org)}},
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

    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp.get("aggregations", {}).get("sankey_flow", {}).get("buckets", [])

    # direction selects the byte counter: upload=client.bytes, download=server.bytes,
    # empty=total. Matches traffic_internal.sankey_data.
    metric = {"upload": "upload_bytes", "download": "download_bytes"}.get(direction, "total_bytes")
    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        port_key = key.get("service", "")
        if port_key == "0" or port_key == 0:
            continue
        bytes_val = int(bucket[metric]["value"] or 0)
        if bytes_val == 0:
            continue
        row = {}
        for i, name in enumerate(level_names):
            raw = key.get(name, "Unknown")
            row[name] = _port_to_service(raw) if name == "service" else raw
        row["bytes"] = bytes_val
        rows.append(row)

    if not rows:
        return {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}

    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    level_totals = {name: _level_totals(rows, name) for name in level_names}

    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: -x[1])[:n]}

    top_sets = {name: _top_n(level_totals[name]) for name in level_names}

    filtered_rows = [r for r in rows if all(r[name] in top_sets[name] for name in level_names)]

    nodes_list: list[dict] = []
    node_index: dict[tuple[int, str], int] = {}

    def _get_node_id(level: int, label: str) -> int:
        key = (level, label)
        if key not in node_index:
            idx = len(nodes_list)
            node_value = level_totals[level_names[level]].get(label, 0)
            nodes_list.append({"id": idx, "label": label, "level": level, "value": node_value})
            node_index[key] = idx
        return node_index[key]

    link_map: dict[tuple[int, int], int] = defaultdict(int)
    for r in filtered_rows:
        ids = [_get_node_id(i, r[name]) for i, name in enumerate(level_names)]
        for i in range(len(ids) - 1):
            link_map[(ids[i], ids[i + 1])] += r["bytes"]

    links_list = [
        {"source": src, "target": tgt, "value": val}
        for (src, tgt), val in link_map.items() if val > 0
    ]
    links_list.sort(key=lambda x: -x["value"])
    links_list = links_list[:30]

    return {
        "nodes": nodes_list,
        "links": links_list,
        "as_country_nodes": [],
        "as_country_links": [],
    }


# ─────────────────────────────────────────────────────────────────
# TI-04: Flow Records Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", after: Optional[dict] = None, page_size: int = 100,
    path_filter: str = "inbound-vip", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None, src_as_org: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    composite_body: dict = {
        "size": page_size,
        "sources": [
            {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
            {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
            {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
        ],
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, src_as_org=src_as_org)}},
        "aggs": {
            "flow_table": {
                "composite": composite_body,
                "aggs": {
                    "upload_bytes": {"sum": {"field": "flow.client.bytes", "missing": 0}},
                    "download_bytes": {"sum": {"field": "flow.server.bytes", "missing": 0}},
                    "total_bytes": {
                        "bucket_script": {
                            "buckets_path": {"up": "upload_bytes", "down": "download_bytes"},
                            "script": "params.up + params.down"
                        }
                    },
                    "total_packets": {"sum": {"field": "flow.packets"}},
                    "session_count": {"cardinality": {"field": "flow.connection_id"}},
                },
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    result = resp["aggregations"]["flow_table"]

    records = []
    for bucket in result["buckets"]:
        key = bucket["key"]
        records.append({
            "client_ip": key.get("client_ip", ""),
            "server_ip": key.get("server_ip", ""),
            "service": _port_to_service(key.get("service_port", "")),
            "bytes": int(bucket["total_bytes"]["value"]),
            "upload_bytes": int(bucket["upload_bytes"]["value"]),
            "download_bytes": int(bucket["download_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key"), "total": len(records)}