"""
Async OpenSearch query builders for Internal Traffic analytics.
Index: fortigate-appid-flow-*
Path filter: flow.traffic.path IN ("intra-lan", "inter-site")
Key dimension: flow.server.l4.port.id (service/port-based).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch._common import (
    FLOW_INDEX, _time_range, _multi_term, _multi_term_any, _bytes_sum, _service_filter, BYTES_DESC,
    service_terms_agg, collapse_service_buckets, resolve_service,
    resolve_top_services, service_histogram_aggs, collapse_chart_bucket,
)
from app.opensearch.query import safe_search, drop_partial_tail, spread_long_sessions, log_zero_bucket_anomaly

SITE_FLOW_MAP: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "dc"),
    "Site_FGT-DRC": ("10.90.150.1", "drc"),
    "Site_FGT_Office": ("10.10.10.10", "drc"),
}


def _get_client(site_name: str = "Site_FGT_Office") -> AsyncOpenSearch:
    _, endpoint = SITE_FLOW_MAP.get(site_name, ("", "drc"))
    return get_dc_client() if endpoint == "dc" else get_drc_client()


def _site_filter(site_name: str) -> dict:
    entry = SITE_FLOW_MAP.get(site_name)
    if not entry or not entry[0]:
        return {"match_none": {}}  # unknown site — see traffic_inbound._site_filter
    return {"term": {"flow.export.ip.addr": entry[0]}}


def _internal_path_filter(traffic_path: str = "all") -> dict:
    if traffic_path == "intra-lan":
        return {"term": {"flow.traffic.path": "intra-lan"}}
    if traffic_path == "inter-site":
        return {"term": {"flow.traffic.path": "inter-site"}}
    # "all" — both paths
    return {
        "bool": {
            "should": [
                {"term": {"flow.traffic.path": "intra-lan"}},
                {"term": {"flow.traffic.path": "inter-site"}},
            ],
            "minimum_should_match": 1,
        }
    }


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str,
    service_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
    traffic_path: str = "all",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name), _internal_path_filter(traffic_path)]
    f = _service_filter(service_filter)  # app-name-first, port fallback (decision 3)
    if f:
        filters.append(f)
    f = _multi_term("flow.client.ip.addr", client_ip)
    if f: filters.append(f)
    f = _multi_term("flow.server.ip.addr", server_ip)
    if f: filters.append(f)
    f = _multi_term("l4.proto.name", protocol.upper())  # proto names are uppercase (TCP/UDP/ICMP); accept any-case input
    if f: filters.append(f)
    if dst_port is not None:
        # Role-based, and consistent with the service_filter above which already
        # resolves to flow.server.l4.port.id — see traffic_flow._base_filters.
        filters.append({"term": {"flow.server.l4.port.id": dst_port}})
    f = _multi_term_any(['flow.in.netif.name', 'flow.in.netif.alias'], ingress_interface)
    if f: filters.append(f)
    f = _multi_term_any(['flow.out.netif.name', 'flow.out.netif.alias'], egress_interface)
    if f: filters.append(f)
    return filters

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", app_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, traffic_path: str = "all",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, service_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, traffic_path=traffic_path, ingress_interface=ingress_interface, egress_interface=egress_interface)}},
        "aggs": {
            "grand_total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "grand_total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
            "session_count": {"cardinality": {"field": "flow.connection_id"}},
            "top_services": service_terms_agg(),
            "top_clients": {
                "terms": {"field": "flow.client.ip.addr", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {"field": "flow.server.ip.addr", "size": 20, "order": BYTES_DESC},
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
            "protocol_dist": {
                "terms": {"field": "l4.proto.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict]:
        return list(aggs.get(agg_name, {}).get("buckets", []))

    service_rows = collapse_service_buckets(_buckets("top_services"))
    total_service_bytes = sum(r["total_bytes"] for r in service_rows) or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    grand_upload = int(aggs.get("grand_total_upload", {}).get("value", 0))
    grand_download = int(aggs.get("grand_total_download", {}).get("value", 0))

    return {
        "total_bytes": grand_upload + grand_download,
        "total_upload": grand_upload,
        "total_download": grand_download,
        "total_sessions": int(aggs.get("session_count", {}).get("value", 0)),
        "top_services": [
            {"service_name": r["service_name"], "service_port": r["service_port"],
             "total_bytes": r["total_bytes"],
             "speed_mbps": (r["total_bytes"] * 8) / duration_s / 1_000_000,
             "percentage": round(r["total_bytes"] / total_service_bytes * 100, 2)}
            for r in service_rows
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
        "ingress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("ingress_breakdown")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
        "protocol_dist": [
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("protocol_dist")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", top_n: int = 20, bucket_seconds: int = 60,
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None, traffic_path: str = "all",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    # Bucket clamp: honor a fine (e.g. 60s) bucket_seconds but never let n_date × N_ports
    # blow past OpenSearch search.max_buckets (10k). 400 date buckets × 20 ports = 8k < 10k.
    # So 60s granularity holds up to ~6.7h ranges, then auto-coarsens. The real bucket used
    # is echoed back and the frontend divides bytes by it, so Mbps stays correct.
    MAX_DATE_BUCKETS = 400
    span_s = max(1, (lte_ms - gte_ms) // 1000)
    bucket_seconds = max(60, bucket_seconds)
    if span_s / bucket_seconds > MAX_DATE_BUCKETS:
        bucket_seconds = -(-span_s // MAX_DATE_BUCKETS)  # ceil division

    base_filter = _base_filters(gte_ms, lte_ms, site_name, service_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, traffic_path=traffic_path)

    # Pass A: global top-N resolved services over the whole range (AppID name first,
    # port fallback for unclassified). This is the STABLE series set for the timeline.
    # `timeout` returns partial results instead of failing empty if the agg runs long.
    top_resp = await safe_search(client, FLOW_INDEX, {
        "size": 0,
        "timeout": "115s",
        "query": {"bool": {"filter": base_filter}},
        "aggs": {"top_services": service_terms_agg(size=min(top_n * 2, 50))},
    })
    app_buckets = top_resp["aggregations"].get("top_services", {}).get("buckets", [])
    charted_names, app_top, port_top, unclassified_labels = resolve_top_services(app_buckets, top_n)
    if not charted_names:
        await log_zero_bucket_anomaly(client, base_filter, site_name=site_name,
            traffic_path=traffic_path, gte_ms=gte_ms, lte_ms=lte_ms, bucket_seconds=bucket_seconds)
        return {"chart_data": [], "service_names": [], "bucket_seconds": bucket_seconds}

    # Pass B: per-bucket values for ONLY the top-N resolved services. by_app pins the
    # classified names and by_port (scoped to unclassified docs) pins the port fallbacks —
    # two single-level terms, so bucket count stays date×(K1+K2), not date×apps×ports.
    # Every series has a value in every bucket → no "holes at 12h" churn.
    interval_str = f"{bucket_seconds}s"
    resp = await safe_search(client, FLOW_INDEX, {
        "size": 0,
        "timeout": "115s",
        "query": {"bool": {"filter": base_filter}},
        "aggs": {"per_minute": service_histogram_aggs(interval_str, app_top, port_top, unclassified_labels)},
    })
    buckets = drop_partial_tail(resp["aggregations"]["per_minute"]["buckets"], bucket_seconds, lte_ms)

    bucket_svc: dict[int, dict[str, float]] = {}
    for bucket in buckets:
        bucket_svc[int(bucket["key"])] = collapse_chart_bucket(bucket)

    # Hybrid correction: re-spread long sessions across their active window (fixes the
    # session-close spike). Re-key on the resolved service so it lines up with the series.
    charted = set(charted_names)
    await spread_long_sessions(
        client, base_filter, "", None, charted,
        bucket_svc, bucket_seconds, gte_ms, lte_ms,
        name_of=lambda s: resolve_service(s.get("flow.application.name"), s.get("flow.server.l4.port.id")),
        source_fields=["flow.application.name", "flow.server.l4.port.id"],
    )

    all_service_bytes: dict[str, float] = {}
    chart_data: list[dict] = []
    for ts_ms in sorted(bucket_svc):
        row: dict = {"timestamp": ts_ms, "timestampMs": ts_ms}
        for svc_name, sb in bucket_svc[ts_ms].items():
            if sb <= 0:
                continue
            row[svc_name] = int(round(sb))
            all_service_bytes[svc_name] = all_service_bytes.get(svc_name, 0.0) + sb
        chart_data.append(row)

    service_names = sorted(all_service_bytes, key=lambda k: all_service_bytes[k], reverse=True)
    return {"chart_data": chart_data, "service_names": service_names, "bucket_seconds": bucket_seconds}


# ─────────────────────────────────────────────────────────────────
# Sankey — Ingress → Service → Egress
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None, traffic_path: str = "all",
    direction: str = "",
) -> dict:
    """Sankey: Ingress → Service → Egress (3 levels).
    direction="upload" weights by flow.client.bytes, "download" by flow.server.bytes,
    else total (upload+download). Structure is identical across directions.
    Paginates composite to collect all unique combinations, then filters to top-10 per level."""
    if client is None:
        client = _get_client(site_name)

    filters = _base_filters(gte_ms, lte_ms, site_name, service_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, traffic_path=traffic_path)
    composite_sources = [
        {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
        {"service_app": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
        {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
        {"egress": {"terms": {"field": "flow.out.netif.name"}}},
    ]

    # Paginate composite to get all unique (ingress, service, egress) combinations.
    # Ponytail: max 5 pages × 2000 = 10,000 combos; covers DC's 10K+ with headroom.
    all_buckets: list[dict] = []
    after_key = None
    for _ in range(5):
        body = {
            "size": 0,
            "query": {"bool": {"filter": filters}},
            "aggs": {
                "sankey_flow": {
                    "composite": {
                        "size": 2000,
                        "sources": composite_sources,
                        **({"after": after_key} if after_key else {}),
                    },
                    "aggs": _bytes_sum(),
                }
            },
        }
        resp = await safe_search(client, FLOW_INDEX, body)
        agg = resp.get("aggregations", {}).get("sankey_flow", {})
        all_buckets.extend(agg.get("buckets", []))
        after_key = agg.get("after_key")
        if not after_key:
            break

    rows: list[dict] = []
    for bucket in all_buckets:
        key = bucket["key"]
        metric = {"upload": "upload_bytes", "download": "download_bytes"}.get(direction, "total_bytes")
        bytes_val = int(bucket[metric]["value"])
        if bytes_val == 0:
            continue
        rows.append({
            "ingress": key.get("ingress", "Unknown"),
            "service": resolve_service(key.get("service_app"), key.get("service_port")),
            "egress": key.get("egress", "Unknown"),
            "bytes": bytes_val,
        })

    if not rows:
        return {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}

    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: -x[1])[:n]}

    ingress_totals = _level_totals(rows, "ingress")
    service_totals = _level_totals(rows, "service")
    egress_totals = _level_totals(rows, "egress")
    level_totals = {"ingress": ingress_totals, "service": service_totals, "egress": egress_totals}

    top_ingress = _top_n(ingress_totals)
    top_services = _top_n(service_totals)
    top_egress = _top_n(egress_totals)

    filtered_rows = [r for r in rows if r["ingress"] in top_ingress and r["service"] in top_services and r["egress"] in top_egress]

    nodes_list: list[dict] = []
    node_index: dict[tuple[int, str], int] = {}

    def _get_node_id(level: int, label: str) -> int:
        key = (level, label)
        if key not in node_index:
            idx = len(nodes_list)
            # Value is needed by d3-sankey for node width calculation
            level_name = ["ingress", "service", "egress"][level]
            node_value = level_totals[level_name].get(label, 0)
            nodes_list.append({"id": idx, "label": label, "level": level, "value": node_value})
            node_index[key] = idx
        return node_index[key]

    link_map: dict[tuple[int, int], int] = defaultdict(int)
    for r in filtered_rows:
        i_id = _get_node_id(0, r["ingress"])
        s_id = _get_node_id(1, r["service"])
        e_id = _get_node_id(2, r["egress"])
        link_map[(i_id, s_id)] += r["bytes"]
        link_map[(s_id, e_id)] += r["bytes"]

    links_list = [{"source": s, "target": t, "value": v} for (s, t), v in link_map.items() if v > 0]
    links_list.sort(key=lambda x: -x["value"])
    links_list = links_list[:30]

    return {
        "nodes": nodes_list,
        "links": links_list,
        "as_country_nodes": [],
        "as_country_links": [],
    }


# ─────────────────────────────────────────────────────────────────
# Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", after: Optional[dict] = None, page_size: int = 100,
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None, traffic_path: str = "all",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    composite_body: dict = {
        "size": page_size,
        "sources": [
            {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
            {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
            {"service_app": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
            {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
        ],
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, service_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, traffic_path=traffic_path)}},
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
            "service": resolve_service(key.get("service_app"), key.get("service_port")),
            "bytes": int(bucket["total_bytes"]["value"]),
            "upload_bytes": int(bucket["upload_bytes"]["value"]),
            "download_bytes": int(bucket["download_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key"), "total": len(records)}