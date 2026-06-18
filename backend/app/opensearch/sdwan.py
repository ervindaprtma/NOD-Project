"""
OpenSearch query builders for telegraf-index* — SD-WAN SLA domain.
Supports 4 links per site: 2× WAN + 2× MPLS (IPSec/ADVPN).
Q-06: ALL queries include exact term filter on measurement_name.keyword.
Q-01: ALL queries include @timestamp range filter with gte/lte.
"""
from __future__ import annotations

from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.schemas.sdwan_resource_vpn import (
    SITE_LINK_COUNT,
    SITE_LINK_LABELS,
    SITE_LINK_TYPES,
    SITE_OS_ENDPOINT,
)


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {
        "range": {
            "@timestamp": {
                "gte": gte_ms,
                "lte": lte_ms,
                "format": "epoch_millis",
            }
        }
    }


def _sdwan_filters(gte_ms: int, lte_ms: int, site_name: str) -> list[dict]:
    """Q-01 + Q-06: time range + exact measurement_name term."""
    return [
        _time_range(gte_ms, lte_ms),
        {"term": {"measurement_name.keyword": site_name}},
    ]


def _get_client_for_site(site_name: str) -> AsyncOpenSearch:
    """Return the correct OpenSearch client for a site based on SITE_OS_ENDPOINT config."""
    endpoint = SITE_OS_ENDPOINT.get(site_name, "dc")
    if endpoint == "drc":
        return get_drc_client()
    return get_dc_client()


def _get_index_for_site(site_name: str) -> str:
    """Return the correct index pattern for a site based on its endpoint.
    All SD-WAN SLA data lives in telegraf-index* on both clusters."""
    # Always use telegraf-index* for SD-WAN metrics regardless of endpoint routing
    return "telegraf-index*"


def _link_count(site_name: str) -> int:
    """How many links to query for this site."""
    return SITE_LINK_COUNT.get(site_name, 2)


def _link_labels(site_name: str) -> dict[str, str]:
    return SITE_LINK_LABELS.get(site_name, {})


def _link_types(site_name: str) -> dict[str, str]:
    return SITE_LINK_TYPES.get(site_name, {})


# ─────────────────────────────────────────────────────────────────
# Pre-fetch Validation (lightweight check before full SLA query)
# ─────────────────────────────────────────────────────────────────


async def validate_sla_data(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
) -> dict:
    """
    Lightweight pre-fetch validation check.
    Returns {"total_hits": int, "source_ip": str}.
    If total_hits == 0, there is no SLA data for this site/time-range.
    Also extracts source_ip from the latest document for frontend labelling.
    """
    if client is None:
        client = _get_client_for_site(site_name)

    body = {
        "size": 1,
        "query": {
            "bool": {
                "filter": _sdwan_filters(gte_ms, lte_ms, site_name)
            }
        },
        "_source": ["tag.source", "tag.device", "measurement_name"],
        "sort": [{"@timestamp": {"order": "desc"}}],
        "track_total_hits": True,
    }

    resp = await client.search(index=_get_index_for_site(site_name), body=body)
    total = resp["hits"]["total"]["value"]
    hits_list = resp["hits"]["hits"]

    source_ip = ""
    if hits_list:
        src = hits_list[0]["_source"]
        tag = src.get("tag", {})
        source_ip = tag.get("source", "")

    return {"total_hits": total, "source_ip": source_ip}


# ─────────────────────────────────────────────────────────────────
# SLA Metrics Timeline (Q-07: single query, all links)
# ─────────────────────────────────────────────────────────────────


async def sla_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    metric: str = "latency",  # latency | jitter | packet_loss
    interval: str = "5m",
) -> list[dict]:
    """
    Q-05: date_histogram with avg sub-agg for every link.
    Returns flat list of points with label + link_type for frontend filtering.
    """
    if client is None:
        client = _get_client_for_site(site_name)

    n_links = _link_count(site_name)
    labels = _link_labels(site_name)
    types = _link_types(site_name)

    # Build aggs for all links
    aggs: dict = {}
    for i in range(1, n_links + 1):
        aggs[f"avg_link{i}"] = {"avg": {"field": f"{site_name}.{metric}_link{i}"}}

    body = {
        "size": 0,
        "query": {"bool": {"filter": _sdwan_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "timeline": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval,
                    "min_doc_count": 0,
                },
                "aggs": aggs,
            }
        },
    }

    resp = await client.search(index=_get_index_for_site(site_name), body=body)
    buckets = resp["aggregations"]["timeline"]["buckets"]

    # Flatten: one entry per (timestamp, linkN)
    result: list[dict] = []
    for b in buckets:
        ts = b["key"]
        for i in range(1, n_links + 1):
            link_key = f"link{i}"
            result.append({
                "timestamp": ts,
                "value": (b[f"avg_link{i}"]["value"] or 0.0),
                "label": labels.get(link_key, link_key),
                "link_type": types.get(link_key, "WAN"),
            })

    return result


# ─────────────────────────────────────────────────────────────────
# Link Status (current) — Q-07: top_hits per site
# ─────────────────────────────────────────────────────────────────


async def sdwan_link_status(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
) -> list[dict]:
    """Returns current link status for all links of the given site."""
    if client is None:
        client = _get_client_for_site(site_name)

    n_links = _link_count(site_name)
    labels = _link_labels(site_name)
    types = _link_types(site_name)

    # Build _source includes for all link fields
    source_includes = ["tag.device"]
    for i in range(1, n_links + 1):
        source_includes.append(f"{site_name}.status_link{i}")
        source_includes.append(f"{site_name}.ifname_link{i}")
        source_includes.append(f"{site_name}.name_sla_sdwan_{i}")

    body = {
        "size": 0,
        "query": {"bool": {"filter": _sdwan_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "latest": {
                "top_hits": {
                    "size": 1,
                    "sort": [{"@timestamp": {"order": "desc"}}],
                    "_source": {"includes": source_includes},
                }
            }
        },
    }

    resp = await client.search(index=_get_index_for_site(site_name), body=body)
    hits = resp["aggregations"]["latest"]["hits"]["hits"]

    if not hits:
        return []

    src = hits[0]["_source"]
    site_data = src.get(site_name, {})

    def _status_label(val) -> str:
        if val is None:
            return "Unknown"
        return "Up" if val == 0 else "Down"

    links = []
    for i in range(1, n_links + 1):
        link_key = f"link{i}"
        links.append({
            "link": link_key,
            "ifname": site_data.get(f"ifname_link{i}", ""),
            "label": labels.get(link_key, link_key),
            "link_type": types.get(link_key, "WAN"),
            "status": _status_label(site_data.get(f"status_link{i}")),
            "sla_target": site_data.get(f"name_sla_sdwan_{i}", ""),
        })

    return links


# ─────────────────────────────────────────────────────────────────
# SLA Summary KPIs
# ─────────────────────────────────────────────────────────────────


async def sla_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
) -> dict:
    """Q-05: avg, max aggregations for all links in window."""
    if client is None:
        client = _get_client_for_site(site_name)

    n_links = _link_count(site_name)
    labels = _link_labels(site_name)
    types = _link_types(site_name)

    aggs: dict = {}
    for i in range(1, n_links + 1):
        aggs[f"avg_latency_link{i}"] = {"avg": {"field": f"{site_name}.latency_link{i}"}}
        aggs[f"max_latency_link{i}"] = {"max": {"field": f"{site_name}.latency_link{i}"}}
        aggs[f"avg_jitter_link{i}"] = {"avg": {"field": f"{site_name}.jitter_link{i}"}}
        aggs[f"avg_packet_loss_link{i}"] = {"avg": {"field": f"{site_name}.packet_loss_link{i}"}}

    body = {
        "size": 0,
        "query": {"bool": {"filter": _sdwan_filters(gte_ms, lte_ms, site_name)}},
        "aggs": aggs,
    }

    resp = await client.search(index=_get_index_for_site(site_name), body=body)
    a = resp["aggregations"]

    return {
        "avg_latency": [(a[f"avg_latency_link{i}"]["value"] or 0.0) for i in range(1, n_links + 1)],
        "max_latency": [(a[f"max_latency_link{i}"]["value"] or 0.0) for i in range(1, n_links + 1)],
        "avg_jitter": [(a[f"avg_jitter_link{i}"]["value"] or 0.0) for i in range(1, n_links + 1)],
        "avg_packet_loss": [(a[f"avg_packet_loss_link{i}"]["value"] or 0.0) for i in range(1, n_links + 1)],
        "labels": [labels.get(f"link{i}", f"link{i}") for i in range(1, n_links + 1)],
        "link_types": [types.get(f"link{i}", "WAN") for i in range(1, n_links + 1)],
    }


# ─────────────────────────────────────────────────────────────────
# Aggregated multi-site query (Q-07: single query, not N+1)
# ─────────────────────────────────────────────────────────────────


async def all_sites_link_status(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_names: Optional[list[str]] = None,
) -> list[dict]:
    """
    Q-07: single query per endpoint. Routes each site to its configured endpoint
    (telegraf or appid) since different sites may live on different OpenSearch clusters.
    """
    if site_names is None:
        from app.core.config import get_settings
        site_names = get_settings().sdwan_sites_list

    # Split sites by endpoint
    telegraf_sites = [s for s in site_names if SITE_OS_ENDPOINT.get(s, "dc") == "dc"]
    appid_sites = [s for s in site_names if SITE_OS_ENDPOINT.get(s, "dc") == "drc"]

    async def _query_endpoint(client: AsyncOpenSearch, sites: list[str], index_pattern: str) -> list[dict]:
        if not sites:
            return []

        # Build _source includes: device tag + all link status fields for all sites
        source_includes = ["tag.device", "measurement_name"]
        for sn in sites:
            n_links = _link_count(sn)
            for i in range(1, n_links + 1):
                source_includes.append(f"{sn}.status_link{i}")
                source_includes.append(f"{sn}.ifname_link{i}")
                source_includes.append(f"{sn}.name_sla_sdwan_{i}")

        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": [
                        _time_range(gte_ms, lte_ms),
                        {"terms": {"measurement_name.keyword": sites}},
                    ]
                }
            },
            "aggs": {
                "by_site": {
                    "terms": {"field": "measurement_name.keyword", "size": len(sites) + 1},
                    "aggs": {
                        "latest": {
                            "top_hits": {
                                "size": 1,
                                "sort": [{"@timestamp": {"order": "desc"}}],
                                "_source": {"includes": source_includes},
                            }
                        }
                    },
                }
            },
        }
        resp = await client.search(index=index_pattern, body=body)
        results: list[dict] = []
        for bucket in resp["aggregations"]["by_site"]["buckets"]:
            sn = bucket["key"]
            hits = bucket["latest"]["hits"]["hits"]
            if not hits:
                continue
            src = hits[0]["_source"]
            site_data = src.get(sn, {})
            n_links = _link_count(sn)
            labels = _link_labels(sn)

            def _status_label(val) -> str:
                if val is None:
                    return "Unknown"
                return "Up" if val == 0 else "Down"

            links = []
            for i in range(1, n_links + 1):
                links.append({
                    "link": f"link{i}",
                    "label": labels.get(f"link{i}", f"link{i}"),
                    "status": _status_label(site_data.get(f"status_link{i}")),
                })
            results.append({"site": sn, "device": src.get("tag.device", ""), "links": links})
        return results

    # Query both endpoints
    results = []
    results += await _query_endpoint(get_dc_client(), telegraf_sites, "telegraf-index*")
    results += await _query_endpoint(get_drc_client(), appid_sites, "telegraf-index*")
    return results
