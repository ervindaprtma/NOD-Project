"""
Async OpenSearch query builders for FortiGate AppID traffic flow analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip>
Routes: DC→dc cluster, DRC+Office→drc cluster
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, cast

from opensearchpy import AsyncOpenSearch

from app.opensearch._common import (
    FLOW_INDEX, _time_range, _multi_term, _multi_term_any, _multi_wildcard, _bytes_sum, BYTES_DESC,
    _bool_query, _bytes3,
)
from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search, composite_all_buckets, drop_partial_tail, spread_long_sessions, log_zero_bucket_anomaly


# ── Site config ──────────────────────────────────────────────────
SITE_SOURCE_IPS: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
    "Site_FGT_Office": "10.10.10.10",
}

# AppID risk is a severity, not a long tail — render Critical→Low regardless of which
# level carries the most bytes (a DNS-heavy site would otherwise bury Critical/High).
_RISK_ORDER: dict[str, int] = {"Critical": 0, "High": 1, "Elevated": 2, "Medium": 3, "Low": 4}


def _get_client(site_name: str = "Site_FGT-DC") -> AsyncOpenSearch:
    if site_name in ("Site_FGT-DRC", "Site_FGT_Office"):
        return get_drc_client()
    return get_dc_client()


def _site_filter(site_name: str) -> dict:
    source_ip = SITE_SOURCE_IPS.get(site_name, "10.80.150.1")
    return {"term": {"flow.export.ip.addr": source_ip}}


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
    app_filter_not: str = "", category_filter_not: str = "",
    client_ip_not: str = "", server_ip_not: str = "", protocol_not: str = "",
    dst_port_not: int | list[int] | None = None, dst_as_org_not: str = "",
    ingress_interface_not: str = "",
    egress_interface_not: str = "",
    **_ignore,  # tolerate any unknown *_not param (stale bookmark / URL edit) → never crash the query
) -> tuple[list[dict], list[dict]]:
    """Returns (filter, must_not). Scope (time/site/path/direction) is include-only; every
    narrowing field has an optional `_not` twin built with the SAME helper but routed to the
    must_not list — a row is dropped if it matches any exclude clause. See _bool_query."""
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter and path_filter != "all":
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    excl: list[dict] = []
    # (include-clause, exclude-clause) per field — same helper, two destinations.
    for inc, exc in (
        (_multi_wildcard("flow.application.name", app_filter),
         _multi_wildcard("flow.application.name", app_filter_not)),
        (_multi_wildcard("flow.application.category", category_filter),
         _multi_wildcard("flow.application.category", category_filter_not)),
        (_multi_term("flow.client.ip.addr", client_ip),
         _multi_term("flow.client.ip.addr", client_ip_not)),
        (_multi_term("flow.server.ip.addr", server_ip),
         _multi_term("flow.server.ip.addr", server_ip_not)),
        # proto names are uppercase (TCP/UDP/ICMP); accept any-case input
        (_multi_term("l4.proto.name", protocol.upper()),
         _multi_term("l4.proto.name", protocol_not.upper())),
        (_multi_wildcard("flow.server.as.org", dst_as_org),
         _multi_wildcard("flow.server.as.org", dst_as_org_not)),
        (_multi_term_any(['flow.in.netif.name', 'flow.in.netif.alias'], ingress_interface),
         _multi_term_any(['flow.in.netif.name', 'flow.in.netif.alias'], ingress_interface_not)),
        (_multi_term_any(['flow.out.netif.name', 'flow.out.netif.alias'], egress_interface),
         _multi_term_any(['flow.out.netif.name', 'flow.out.netif.alias'], egress_interface_not)),
    ):
        if inc: filters.append(inc)
        if exc: excl.append(exc)
    if dst_port is not None:
        # Role-based, not direction-based: flow.dst.l4.port.id is the destination of
        # each leg, so it only matches the request leg and drops the (much larger)
        # response leg. flow.server.l4.port.id is stable across both legs and matches
        # what the top_services agg buckets on.
        filters.append({"term": {"flow.server.l4.port.id": dst_port}})
    if dst_port_not is not None:
        # int → term, list (multi-port exclude) → terms
        excl.append({"terms": {"flow.server.l4.port.id": dst_port_not}} if isinstance(dst_port_not, list)
                    else {"term": {"flow.server.l4.port.id": dst_port_not}})
    return filters, excl


# ─────────────────────────────────────────────────────────────────
# TF-01: Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
    exclude: dict | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": _bool_query(*_base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org, ingress_interface=ingress_interface, egress_interface=egress_interface, **(exclude or {}))),
        "aggs": {
            "grand_total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "grand_total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
            "top_apps": {
                "terms": {"field": "flow.application.name", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "app_categories": {
                "terms": {"field": "flow.application.category", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            # On the `internet` path the internal host is the client on BOTH legs —
            # client/server roles stay fixed across a correlated flow. So the external
            # org is flow.server.as.org for upload and download alike; aggregating
            # client.as.org on the download leg only ever yields "Private".
            "top_dst_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.server.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_dst_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.server.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"countries": {"terms": {"field": "flow.dst.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.src.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
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
            "egress_breakdown": {
                "terms": {"field": "flow.out.netif.alias", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "ingress_breakdown": {
                "terms": {"field": "flow.in.netif.alias", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            # Unique session count for the timeframe: cardinality of correlation_id (one
            # logical client-port↔server-port connection; connection_id is a coarse
            # conversation key that collapses ~180x too many sessions into one).
            "session_count": {"cardinality": {"field": "flow.correlation_id"}},
        },
    }

    # Risk rides in the MAIN query — it's keyword on every index, so it never errors and
    # works today. Internet path only (inbound/internal don't render it).
    if path_filter == "internet":
        cast(dict, body["aggs"])["top_risk"] = {
            "terms": {"field": "flow.application.risk", "size": 8, "order": BYTES_DESC}, "aggs": _bytes_sum(),
        }

    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]

    # Vendor/Tech run in a SEPARATE guarded query (Internet path only). On the pre-upgrade
    # index these two are still `text`, which errors the WHOLE _search — isolating them means
    # that error empties only these two panels, never the main summary/page. They self-fill
    # once every index in the window maps them as keyword (post-rollover). safe_search returns
    # {"aggregations": {}} on any error → .get(...) → [] → empty panels, no exception here.
    enrich_aggs: dict[str, Any] = {}
    if path_filter == "internet":
        enrich_body = {
            "size": 0,
            "query": body["query"],
            "aggs": {
                "top_vendor": {"terms": {"field": "flow.application.vendor", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()},
                "top_tech": {"terms": {"field": "flow.application.tech", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()},
            },
        }
        enrich_aggs = (await safe_search(client, FLOW_INDEX, enrich_body)).get("aggregations", {}) or {}

    def _buckets(agg_name: str) -> list[dict[str, Any]]:
        buckets: list[dict[str, Any]] = aggs.get(agg_name, {}).get("buckets", [])
        return buckets

    def _merge_filter_buckets(upload_agg: str, download_agg: str, inner_agg: str) -> list[dict]:
        merged: dict[str, int] = {}
        for agg_name in (upload_agg, download_agg):
            filter_result = aggs.get(agg_name, {})
            for b in filter_result.get(inner_agg, {}).get("buckets", []):
                key = b["key"]
                merged[key] = merged.get(key, 0) + int(b["total_bytes"]["value"])
        return [{"key": k, "total_bytes": v} for k, v in sorted(merged.items(), key=lambda x: -x[1])[:20]]

    as_org_buckets = _merge_filter_buckets("top_dst_upload", "top_dst_download", "as_orgs")
    country_buckets = _merge_filter_buckets("top_country_upload", "top_country_download", "countries")

    total_app_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("top_apps")) or 1
    total_proto_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("protocol_dist")) or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    # Calculate totals in Python (pipeline aggregations can't sort, sum works fine)
    grand_upload = int(aggs.get("grand_total_upload", {}).get("value", 0))
    grand_download = int(aggs.get("grand_total_download", {}).get("value", 0))

    return {
        "total_bytes": grand_upload + grand_download,
        "total_upload": grand_upload,
        "total_download": grand_download,
        "total_sessions": int(aggs.get("session_count", {}).get("value", 0)),
        "top_apps": [
            {"app_name": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
             "percentage": round(int(b["total_bytes"]["value"]) / total_app_bytes * 100, 2)}
            for b in _buckets("top_apps")
        ],
        "app_categories": [
            {"category_name": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "count": b["doc_count"]}
            for b in _buckets("app_categories")
        ],
        "top_dst_as_org": [
            {"org_name": b["key"], "total_bytes": b["total_bytes"]}
            for b in as_org_buckets
        ],
        "top_dst_as_country": [
            {"country": b["key"], "total_bytes": b["total_bytes"], "flag_code": ""}
            for b in country_buckets
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
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "percentage": round(int(b["total_bytes"]["value"]) / total_proto_bytes * 100, 2)}
            for b in _buckets("protocol_dist")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
        "ingress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("ingress_breakdown")
        ],
        # AppID enrichment. Risk from the main query (keyword, safe); Vendor/Tech from the
        # isolated guarded query above (empty if it errored on a text-mapped index — the main
        # summary is unaffected, so the page never shows the degradation banner for these).
        "top_vendor": [
            {"vendor": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in enrich_aggs.get("top_vendor", {}).get("buckets", [])
        ],
        "top_tech": [
            {"tech": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in enrich_aggs.get("top_tech", {}).get("buckets", [])
        ],
        "top_risk": [
            {"risk": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in sorted(_buckets("top_risk"), key=lambda b: _RISK_ORDER.get(b["key"], 99))
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TF-02: Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", top_n: int = 50, path_filter: str = "internet",
    bucket_seconds: int = 60, app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    exclude: dict | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    # base_filter stays the include list (reused by spread_long_sessions / anomaly logging,
    # which already operate on the post-exclude charted app set); excludes apply to the main query.
    base_filter, base_excl = _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org, **(exclude or {}))
    body = {
        "size": 0,
        "query": _bool_query(base_filter, base_excl),
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str, "min_doc_count": 1},
                "aggs": {
                    "top_apps": {
                        "terms": {"field": "flow.application.name", "size": top_n, "order": BYTES_DESC},
                        "aggs": _bytes_sum(),
                    }
                },
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    result = resp["aggregations"]["per_minute"]

    # Base: per-bucket per-app bytes by @timestamp.
    bucket_app: dict[int, dict[str, float]] = {}
    for bucket in drop_partial_tail(result["buckets"], bucket_seconds, lte_ms):
        app_map: dict[str, float] = {}
        for app_bucket in bucket["top_apps"]["buckets"]:
            app_bytes = int(app_bucket["total_bytes"]["value"])
            if app_bytes <= 0:
                continue
            app_map[app_bucket["key"]] = app_map.get(app_bucket["key"], 0.0) + float(app_bytes)
        bucket_app[int(bucket["key"])] = app_map

    # Session-close-spike fix: re-spread long sessions across their active window (keyed by app).
    charted = {app for m in bucket_app.values() for app in m}
    await spread_long_sessions(
        client, base_filter, "flow.application.name", lambda x: x, charted,
        bucket_app, bucket_seconds, gte_ms, lte_ms, key_filter_values=list(charted),
        must_not=base_excl,
    )

    app_totals: dict[str, float] = {}
    chart_data: list[dict[str, Any]] = []
    for ts_ms in sorted(bucket_app):
        row: dict[str, Any] = {"timestamp": ts_ms, "timestampMs": ts_ms}
        for app_name, ab in bucket_app[ts_ms].items():
            if ab <= 0:
                continue
            row[app_name] = int(round(ab))
            app_totals[app_name] = app_totals.get(app_name, 0.0) + ab
        chart_data.append(row)

    # Sort by total bytes descending (not alphabetically) so frontend gets top apps first
    sorted_apps = [name for name, _ in sorted(app_totals.items(), key=lambda x: -x[1])]
    if not app_totals:
        await log_zero_bucket_anomaly(client, base_filter, site_name=site_name,
            traffic_path=path_filter, gte_ms=gte_ms, lte_ms=lte_ms, bucket_seconds=bucket_seconds,
            must_not=base_excl)
    return {"chart_data": chart_data, "app_names": sorted_apps, "bucket_seconds": bucket_seconds}


# ─────────────────────────────────────────────────────────────────
# TF-03: Sankey — direction-aware pola
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    exclude: dict | None = None,
) -> dict:
    """Sankey for Internet traffic flow.

    Upload:   Ingress → Apps → Egress → AS Org
    Download: AS Org → Ingress → Apps → Egress
    Node order is per-direction (cleaner left-to-right view); Upload weights links
    by flow.client.bytes, Download by flow.server.bytes, empty direction by their
    sum. No zone filter — path_filter is the separate selector.
    """
    if client is None:
        client = _get_client(site_name)

    # Per-direction node order only. direction is NOT passed to _base_filters (no zone
    # filter); path_filter stays the separate selector.
    if direction == "download":
        # Download: AS Org → Ingress → Apps → Egress
        sources = [
            {"as_org": {"terms": {"field": "flow.server.as.org", "missing_bucket": True}}},
            {"ingress": {"terms": {"field": "flow.in.netif.alias", "missing_bucket": True}}},
            {"app": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
            {"egress": {"terms": {"field": "flow.out.netif.alias", "missing_bucket": True}}},
        ]
        level_names = ["as_org", "ingress", "app", "egress"]
    else:
        # Upload (and empty): Ingress → Apps → Egress → AS Org
        sources = [
            {"ingress": {"terms": {"field": "flow.in.netif.alias", "missing_bucket": True}}},
            {"app": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
            {"egress": {"terms": {"field": "flow.out.netif.alias", "missing_bucket": True}}},
            {"as_org": {"terms": {"field": "flow.server.as.org", "missing_bucket": True}}},
        ]
        level_names = ["ingress", "app", "egress", "as_org"]

    # Paginate the composite so the top-byte flows are never lost to a 1000-bucket
    # key-order slice (BUG-1); _bytes_sum() gives a server-side total_bytes so the
    # empty-direction case needs no special branch. See Documentation/SANKEY_BUGS_ANALYSIS.md.
    q = _bool_query(*_base_filters(gte_ms, lte_ms, site_name, path_filter, "", app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org, **(exclude or {})))
    buckets = await composite_all_buckets(client, FLOW_INDEX, q, sources, _bytes_sum())

    # direction selects the byte counter: upload=client.bytes, download=server.bytes,
    # empty=total. Matches traffic_internal.sankey_data.
    metric = {"upload": "upload_bytes", "download": "download_bytes"}.get(direction, "total_bytes")
    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        bytes_val = int(bucket[metric]["value"] or 0)
        if bytes_val == 0:
            continue
        # missing_bucket keys are PRESENT with value None → `.get(name) or "Unknown"`,
        # not `.get(name, "Unknown")` (which would keep None and label a node "None").
        row: dict = {}
        for name in level_names:
            row[name] = key.get(name) or "Unknown"
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
# TF-04: Flow Records Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", after: Optional[dict] = None, page_size: int = 100,
    path_filter: str = "internet", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    exclude: dict | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    composite_body: dict = {
        "size": page_size,
        "sources": [
            {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
            {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
            {"app_name": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
        ],
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": _bool_query(*_base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org, **(exclude or {}))),
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
                    "session_count": {"cardinality": {"field": "flow.correlation_id"}},
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
            "app_name": key.get("app_name", ""),
            "bytes": int(bucket["total_bytes"]["value"]),
            "upload_bytes": int(bucket["upload_bytes"]["value"]),
            "download_bytes": int(bucket["download_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key")}


async def top_dst_as_orgs(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
    site_name: str = "Site_FGT-DC",
) -> list[dict]:
    """Q-02: terms agg on flow.server.as.org — top destination AS organizations."""
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name), {"bool": {"must_not": [{"term": {"flow.server.as.org": "Private"}}]}}]}},
        "aggs": {
            "top_as_orgs": {
                "terms": {
                    "field": "flow.server.as.org",
                    "size": min(size, 500),
                    "order": BYTES_DESC,
                },
                "aggs": _bytes_sum(),
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp["aggregations"]["top_as_orgs"]["buckets"]
    return [
        {"as_org": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
         "upload_bytes": int(b["upload_bytes"]["value"]),
         "download_bytes": int(b["download_bytes"]["value"])}
        for b in buckets
    ]


async def top_applications(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
    site_name: str = "Site_FGT-DC",
) -> list[dict]:
    """Q-02: terms agg with explicit size, sum sub-agg on byte fields."""
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name)]}},
        "aggs": {
            "top_apps": {
                "terms": {
                    "field": "flow.application.name",
                    "size": min(size, 500),
                    "order": BYTES_DESC,
                },
                "aggs": _bytes_sum(),
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp["aggregations"]["top_apps"]["buckets"]
    return [
        {"application": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
         "upload_bytes": int(b["upload_bytes"]["value"]),
         "download_bytes": int(b["download_bytes"]["value"])}
        for b in buckets
    ]


async def total_throughput(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
) -> dict[str, int]:
    """Return {total_bytes, total_upload, total_download} for the time range.

    Annotation was `-> int` while the body returned a dict; both call sites already
    branch on isinstance(..., dict), so this corrects the signature to match reality
    rather than changing behaviour.
    """
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name)]}},
        "aggs": {
            "total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]
    return {
        "total_bytes": int(aggs["total_upload"]["value"] or 0) + int(aggs["total_download"]["value"] or 0),
        "total_upload": int(aggs["total_upload"]["value"] or 0),
        "total_download": int(aggs["total_download"]["value"] or 0),
    }


async def appid_flow_alert_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    app_filter: str = "",
    protocol: str = "",
    dst_port: int | None = None,
    app_not: str = "",
    protocol_not: str = "",
    port_not: int | None = None,
) -> dict[str, dict[str, float | int]]:
    """One query → per-`flow.traffic.path` throughput for alerting.

    Splits traffic by path (internet / inbound-vip / inter-site / intra-lan) plus a
    `_wan` all-paths aggregate, returning both a window-average **rate** (Mbps) and raw
    **bytes** per node. The alert extractor picks a node + metric from the rule's
    metric_field. Rate is preferred for thresholds because it's window-size-stable;
    bytes are kept for volume-cap rules and legacy (WAN-total) compatibility.

    ponytail: single sum-over-window → avg==max rate (no per-bucket series). Upgrade to a
    date_histogram like interface_stats only if a rule needs a true peak-minute max.
    """
    if client is None:
        client = _get_client(site_name)
    # Optional app/protocol/port scoping — same fields the flow pages filter on
    # (flow.application.name / l4.proto.name / flow.server.l4.port.id).
    scope: list[dict] = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    scope_not: list[dict] = []
    f = _multi_wildcard("flow.application.name", app_filter)
    if f:
        scope.append(f)
    f = _multi_wildcard("flow.application.name", app_not)   # exclude twin → must_not
    if f:
        scope_not.append(f)
    f = _multi_term("l4.proto.name", protocol.upper())  # proto names are uppercase
    if f:
        scope.append(f)
    f = _multi_term("l4.proto.name", protocol_not.upper())
    if f:
        scope_not.append(f)
    if dst_port is not None:
        scope.append({"term": {"flow.server.l4.port.id": dst_port}})
    if port_not is not None:
        scope_not.append({"term": {"flow.server.l4.port.id": port_not}})
    body = {
        "size": 0,
        "query": _bool_query(scope, scope_not),
        "aggs": {
            "by_path": {
                "terms": {"field": "flow.traffic.path", "size": 12},
                "aggs": {
                    "up": {"sum": {"field": "flow.client.bytes", "missing": 0}},
                    "down": {"sum": {"field": "flow.server.bytes", "missing": 0}},
                },
            },
            "up_all": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "down_all": {"sum": {"field": "flow.server.bytes", "missing": 0}},
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp.get("aggregations", {})
    secs = max(1.0, (lte_ms - gte_ms) / 1000.0)
    to_mbps = lambda b: b * 8 / secs / 1e6

    def _node(up: int, dn: int) -> dict[str, float | int]:
        return {
            "upload_mbps": to_mbps(up), "download_mbps": to_mbps(dn), "total_mbps": to_mbps(up + dn),
            "upload_bytes": up, "download_bytes": dn, "total_bytes": up + dn,
        }

    out: dict[str, dict[str, float | int]] = {}
    for b in aggs.get("by_path", {}).get("buckets", []):
        out[b["key"]] = _node(int(b["up"]["value"] or 0), int(b["down"]["value"] or 0))
    out["_wan"] = _node(int(aggs.get("up_all", {}).get("value") or 0),
                        int(aggs.get("down_all", {}).get("value") or 0))
    return out


async def appid_flow_app_scan(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    path: str = "internet",
    top_n: int = 10,
    app_not: str = "",
    protocol_not: str = "",
    port_not: int | None = None,
) -> list[dict[str, Any]]:
    """Phase-1 scan: per-**application** throughput on a path, for the "monitor all apps" alert.

    One flat `terms` on flow.application.name (top-N by bytes) → each app's window-average Mbps.
    Same rate math as flow_summary.top_apps / appid_flow_alert_summary. `path` = the
    flow.traffic.path value ("internet"/"inbound-vip"/…) or "" for all paths. The exclude twins
    (app_not/protocol_not/port_not) reuse the flow-page filter helpers so "scan all EXCEPT X" works.
    Returns [{app, upload_mbps, download_mbps, total_mbps, upload_bytes, download_bytes, total_bytes}].
    """
    if client is None:
        client = _get_client(site_name)
    scope: list[dict] = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path:
        scope.append({"term": {"flow.traffic.path": path}})
    scope_not: list[dict] = []
    f = _multi_wildcard("flow.application.name", app_not)
    if f:
        scope_not.append(f)
    f = _multi_term("l4.proto.name", protocol_not.upper())  # proto names are uppercase
    if f:
        scope_not.append(f)
    if port_not is not None:
        scope_not.append({"term": {"flow.server.l4.port.id": port_not}})
    body = {
        "size": 0,
        "query": _bool_query(scope, scope_not),
        "aggs": {
            "by_app": {
                "terms": {"field": "flow.application.name", "size": max(1, top_n), "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    secs = max(1.0, (lte_ms - gte_ms) / 1000.0)
    to_mbps = lambda b: b * 8 / secs / 1e6  # noqa: E731
    out: list[dict[str, Any]] = []
    for b in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
        t, u, d = _bytes3(b)
        out.append({
            "app": b["key"],
            "upload_mbps": to_mbps(u), "download_mbps": to_mbps(d), "total_mbps": to_mbps(t),
            "upload_bytes": u, "download_bytes": d, "total_bytes": t,
        })
    return out


async def appid_flow_app_detail(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    path: str = "internet",
    apps: list[str] | None = None,
    internet_path: bool = True,
    top: int = 3,
) -> dict[str, dict[str, Any]]:
    """Phase-2 enrichment (only run while a scan rule is breaching, scoped to the ≤2 offenders):
    per-app WHO (source IPs) / WHERE OUT (egress interface) / TO WHOM (dest AS org) + volume.

    Mirrors the Traffic Internet panels: client IPs = top_clients, dst org = flow.server.as.org
    (never client.as.org → "Private"), egress = flow.out.netif.alias. On the internet path the
    egress terms are scoped to out-zone=internet so the WAN link is named, not the LAN VLAN a
    download leg egresses (byte-ordering would otherwise rank the VLAN first).
    Returns {app: {total_bytes, src_ips:[{ip,bytes}], egress:[names], dst_orgs:[names]}}.
    """
    apps = apps or []
    if not apps:
        return {}
    if client is None:
        client = _get_client(site_name)
    scope: list[dict] = [_time_range(gte_ms, lte_ms), _site_filter(site_name),
                         {"terms": {"flow.application.name": apps}}]
    if path:
        scope.append({"term": {"flow.traffic.path": path}})
    _sort = {"sort_bytes": {"sum": {"field": "flow.bytes", "missing": 0}}}
    egress_terms = {"terms": {"field": "flow.out.netif.alias", "size": top, "order": BYTES_DESC},
                    "aggs": _sort}
    egress_agg = (
        {"filter": {"term": {"flow.out.netif.sec.zone.name": "internet"}},
         "aggs": {"ifaces": egress_terms}}
        if internet_path else egress_terms
    )
    body = {
        "size": 0,
        "query": _bool_query(scope),
        "aggs": {
            "by_app": {
                "terms": {"field": "flow.application.name", "include": apps, "size": len(apps)},
                "aggs": {
                    **_bytes_sum(),
                    "src_ips": {"terms": {"field": "flow.client.ip.addr", "size": top, "order": BYTES_DESC},
                                "aggs": _bytes_sum()},
                    "dst_orgs": {"terms": {"field": "flow.server.as.org", "size": top, "order": BYTES_DESC},
                                 "aggs": _sort},
                    "egress": egress_agg,
                },
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    out: dict[str, dict[str, Any]] = {}
    for ab in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
        t, _u, _d = _bytes3(ab)
        src_ips = [{"ip": x["key"], "bytes": _bytes3(x)[0]} for x in ab.get("src_ips", {}).get("buckets", [])]
        dst_orgs = [x["key"] for x in ab.get("dst_orgs", {}).get("buckets", [])]
        eg = ab.get("egress", {})
        eg_buckets = eg.get("ifaces", {}).get("buckets", []) if internet_path else eg.get("buckets", [])
        egress = [x["key"] for x in eg_buckets]
        out[ab["key"]] = {"total_bytes": t, "src_ips": src_ips, "dst_orgs": dst_orgs, "egress": egress}
    return out


# ─────────────────────────────────────────────────────────────────
# TF-05: Raw Flow Records
# ─────────────────────────────────────────────────────────────────


async def raw_flows(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    page_size: int = 25,
    search_after: Optional[list] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    filters: Optional[dict] = None,
    site_name: str = "Site_FGT-DC",
    path_filter: str = "internet",
    direction: str = "",
) -> dict:
    """
    Q-03: _source includes only required fields.
    Q-04: no scroll API — uses search_after.
    Q-08: valid search_after pagination.
    Routes to correct cluster per site (dc/drc).
    Returns {"records": [...], "search_after": [...], "total_hits": ...}
    """
    if client is None:
        client = _get_client(site_name)

    if page_size > 500:
        raise ValueError("page_size must be <= 500")

    source_fields = [
        "@timestamp",
        "flow.client.ip.addr",
        "flow.server.ip.addr",
        "flow.application.name",
        "flow.application.category",
        "l4.proto.name",
        "flow.server.l4.port.id",
        "flow.client.bytes",
        "flow.server.bytes",
        "flow.packets",
        "flow.in.netif.alias",
        "flow.out.netif.alias",
        "flow.correlation_id",
        "flow.correlation_direction",
        "flow.traffic.path",
        "flow.application.classification_method",
    ]

    must_filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]

    if path_filter and path_filter != "all":
        must_filters.append({"term": {"flow.traffic.path": path_filter}})

    if direction == "upload":
        must_filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        must_filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        must_filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        must_filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})

    def _raw_clause(key: str, values) -> dict | None:
        """One filter key → its OS clause. Shared by include (key) and exclude (key_not) so
        the two stay symmetric. dst_port uses the role-based flow.server.l4.port.id (stable
        across both legs — matches the summary panels); interface matches name OR alias (the
        table shows the alias, the breakdown panels the name, both feed one filter bar)."""
        if not values:
            return None
        vals = values if isinstance(values, list) else [values]
        field = {
            "client_ip": "flow.client.ip.addr", "server_ip": "flow.server.ip.addr",
            "application": "flow.application.name", "category": "flow.application.category",
            "protocol": "l4.proto.name", "dst_port": "flow.server.l4.port.id",
            "correlation_id": "flow.correlation_id",
        }.get(key)
        if field:
            return {"terms": {field: vals}}
        if key == "ingress_interface":
            return {"bool": {"should": [{"terms": {"flow.in.netif.name": vals}},
                                        {"terms": {"flow.in.netif.alias": vals}}], "minimum_should_match": 1}}
        if key == "egress_interface":
            return {"bool": {"should": [{"terms": {"flow.out.netif.name": vals}},
                                        {"terms": {"flow.out.netif.alias": vals}}], "minimum_should_match": 1}}
        return None

    _RAW_FILTER_KEYS = ("client_ip", "server_ip", "application", "category", "protocol",
                        "dst_port", "ingress_interface", "egress_interface", "correlation_id")
    must_not_filters: list[dict] = []
    if filters:
        for k in _RAW_FILTER_KEYS:
            c = _raw_clause(k, filters.get(k))
            if c:
                must_filters.append(c)
            c = _raw_clause(k, filters.get(k + "_not"))   # exclude twin → must_not
            if c:
                must_not_filters.append(c)

    sort_field = sort_by if sort_by else "@timestamp"
    sort_order = "desc" if sort_dir == "desc" else "asc"
    sort_clause = [
        {sort_field: {"order": sort_order}},
        {"_id": {"order": sort_order}},
    ]

    body: dict = {
        "size": page_size,
        "query": _bool_query(must_filters, must_not_filters),
        "sort": sort_clause,
        "_source": {"includes": source_fields},
        # Accurate total, not the default 10k cap — otherwise "records" (capped) would
        # read as smaller than the uncapped session count below, which looks broken.
        "track_total_hits": True,
        # Distinct sessions across the WHOLE filtered set (not just this page): one
        # correlation_id = one client-port↔server-port connection. Safe on scroll —
        # correlation_id is a keyword, so cardinality uses doc_values, not fielddata.
        # ponytail: recomputed per page; gate on `search_after is None` if it ever bites.
        "aggs": {"session_count": {"cardinality": {"field": "flow.correlation_id"}}},
    }

    if search_after:
        body["search_after"] = search_after

    try:
        resp = await client.search(index=FLOW_INDEX, body=body, request_timeout=30)
    except Exception as e:
        return {"records": [], "search_after": None, "total_hits": 0, "total_sessions": 0, "error": str(e)}

    hits = resp["hits"]["hits"]
    records = []
    for hit in hits:
        src = hit["_source"]
        records.append({
            "timestamp": src.get("@timestamp", ""),
            "client_ip": src.get("flow.client.ip.addr", ""),
            "server_ip": src.get("flow.server.ip.addr", ""),
            "application": src.get("flow.application.name", ""),
            "category": src.get("flow.application.category", ""),
            "protocol": src.get("l4.proto.name", ""),
            # Service port (role-based), so the column agrees with the dst_port filter
            # and with the service/port dimension used across the summary panels. The
            # per-leg destination port is an ephemeral port on the response leg.
            "dst_port": src.get("flow.server.l4.port.id") or 0,
            "total_bytes": (src.get("flow.client.bytes", 0) or 0) + (src.get("flow.server.bytes", 0) or 0),
            "packets": src.get("flow.packets", 0),
            "ingress_interface": src.get("flow.in.netif.alias", ""),
            "egress_interface": src.get("flow.out.netif.alias", ""),
            "correlation_id": src.get("flow.correlation_id", ""),
            "correlation_direction": src.get("flow.correlation_direction", ""),
            "classification": src.get("flow.application.classification_method", ""),
            "path": src.get("flow.traffic.path", ""),
        })

    next_search_after = hits[-1]["sort"] if hits else None

    return {
        "records": records,
        "search_after": next_search_after,
        "total_hits": resp["hits"]["total"]["value"]
        if isinstance(resp["hits"]["total"], dict)
        else resp["hits"]["total"],
        "total_sessions": int(resp.get("aggregations", {}).get("session_count", {}).get("value", 0)),
    }