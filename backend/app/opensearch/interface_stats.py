"""
OpenSearch query builders for telegraf-index* — Interface Stats (fgt_iface_stats) domain.
Q-06: ALL queries include exact term filter on measurement_name.keyword.
Q-01: ALL queries include @timestamp range filter with gte/lte.
Q-05/Q-07: Single query with nested aggs — no N+1 per interface.

Uses hardcoded tag.ifIndex per site — only 4 WAN/MPLS interfaces per site.
"""
from __future__ import annotations

from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search

# ── Site-to-source-IP mapping ────────────────────────────────────
SITE_SOURCE_MAP: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
    "Site_FGT_Office": "10.10.10.10",
}

# ── Site-to-OpenSearch-endpoint routing ──────────────────────────
# DC + Office → dc cluster (10.80.150.108:9200)
# DRC → drc cluster (10.90.150.108:9200)
SITE_ENDPOINT: dict[str, str] = {
    "Site_FGT-DC": "dc",
    "Site_FGT-DRC": "drc",
    "Site_FGT_Office": "dc",
}

# ── Hardcoded ifIndex per site + friendly labels ─────────────────
SITE_IFINDEX_MAP: dict[str, dict[str, str]] = {
    "Site_FGT-DC": {
        "3": "WAN LinkNet",
        "4": "WAN iForte",
        "39": "MPLS LinkNet",
        "38": "MPLS iForte",
    },
    "Site_FGT-DRC": {
        "7": "WAN LinkNet",
        "8": "WAN iForte",
        "39": "MPLS LinkNet",
        "38": "MPLS iForte",
    },
    "Site_FGT_Office": {
        "16": "WAN LDP",
        "17": "WAN iForte",
        "14": "MPLS LinkNet",
        "15": "MPLS iForte",
    },
}

# ── Display sort order: WAN first, MPLS second; vendor grouping ──
# Grid layout: Col1=items[0,2], Col2=items[1,3]
#   WAN vendor A | WAN vendor B
#   MPLS vendor A | MPLS vendor B
SITE_IFACE_SORT_ORDER: dict[str, dict[str, int]] = {
    "Site_FGT-DC": {
        "3": 0,   # WAN LinkNet  → Col1 Row1
        "4": 1,   # WAN iForte   → Col2 Row1
        "39": 2,  # MPLS LinkNet → Col1 Row2
        "38": 3,  # MPLS iForte  → Col2 Row2
    },
    "Site_FGT-DRC": {
        "7": 0,   # WAN LinkNet  → Col1 Row1
        "8": 1,   # WAN iForte   → Col2 Row1
        "39": 2,  # MPLS LinkNet → Col1 Row2
        "38": 3,  # MPLS iForte  → Col2 Row2
    },
    "Site_FGT_Office": {
        "16": 0,  # WAN LDP      → Col1 Row1
        "17": 1,  # WAN iForte   → Col2 Row1
        "14": 2,  # MPLS LinkNet → Col1 Row2
        "15": 3,  # MPLS iForte  → Col2 Row2
    },
}

INDEX_PATTERN: str = "telegraf-index*"


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


def _parse_interval_seconds(interval: str) -> int:
    """Parse OpenSearch interval string to seconds. E.g. '60s' -> 60, '5m' -> 300, '15m' -> 900."""
    interval = interval.strip()
    if interval.endswith("s"):
        return int(interval[:-1])
    elif interval.endswith("m"):
        return int(interval[:-1]) * 60
    elif interval.endswith("h"):
        return int(interval[:-1]) * 3600
    return 60  # fallback


def _get_client_for_site(site_name: str) -> AsyncOpenSearch:
    """Return the correct OpenSearch client for a site based on SITE_ENDPOINT config."""
    endpoint = SITE_ENDPOINT.get(site_name, "dc")
    if endpoint == "drc":
        return get_drc_client()
    return get_dc_client()


# ─────────────────────────────────────────────────────────────────
# Interface Stats Timeline (Q-07: single query, hardcoded interfaces)
# ─────────────────────────────────────────────────────────────────


async def interface_stats_timeline(
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    interval: str = "60s",
    client: Optional[AsyncOpenSearch] = None,
) -> dict:
    """
    Fetch per-interface stats timeline from OpenSearch.

    Only queries the 4 hardcoded WAN/MPLS interfaces per site
    defined in SITE_IFINDEX_MAP. No dynamic discovery.

    Q-01: @timestamp range filter with gte/lte.
    Q-06: exact measurement_name term filter.
    Q-05: aggregation in OpenSearch, not Python.
    """
    source_ip = SITE_SOURCE_MAP.get(site_name)
    if not source_ip:
        raise ValueError(f"Unknown site_name: {site_name}")

    iface_map = SITE_IFINDEX_MAP.get(site_name, {})
    if not iface_map:
        raise ValueError(f"No interface mapping for site: {site_name}")

    if_indexes = list(iface_map.keys())

    if client is None:
        client = _get_client_for_site(site_name)

    # Parse interval seconds for Mbps calculation
    interval_seconds = _parse_interval_seconds(interval)

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    _time_range(gte_ms, lte_ms),
                    {"term": {"measurement_name.keyword": "fgt_iface_stats"}},
                    {"term": {"tag.source.keyword": source_ip}},
                    {"terms": {"tag.ifIndex.keyword": if_indexes}},
                ]
            }
        },
        "aggs": {
            "by_interface": {
                "terms": {
                    "field": "tag.ifIndex.keyword",
                    "size": len(if_indexes),
                },
                "aggs": {
                    "by_time": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 1,
                        },
                        "aggs": {
                            "max_in_octets": {
                                "max": {"field": "fgt_iface_stats.ifHCInOctets"}
                            },
                            "max_out_octets": {
                                "max": {"field": "fgt_iface_stats.ifHCOutOctets"}
                            },
                            "speed_mbps": {
                                "max": {"field": "fgt_iface_stats.ifHighSpeed_Mbps"}
                            },
                            "oper_status": {
                                "max": {"field": "fgt_iface_stats.ifOperStatus"}
                            },
                        },
                    },
                },
            }
        },
    }

    resp = await safe_search(client, INDEX_PATTERN, body)
    return {"aggregations": resp["aggregations"], "interval_seconds": interval_seconds}


async def interface_stats_summary(
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
    bucket: str = "60s",
    client: Optional[AsyncOpenSearch] = None,
) -> dict:
    """Per-interface bandwidth for alerting, keyed by ifIndex.

    ifHCInOctets/ifHCOutOctets are cumulative SNMP counters, so bandwidth is their
    *rate of change*: a date-histogram + derivative pipeline agg gives the octet delta
    per bucket, converted to Mbps. avg/max are computed across the per-bucket rates.

    A rule's evaluation window must span ≥ 2 buckets or there's no derivative (the
    builder enforces a 2-minute minimum). Counter resets (reboot → negative delta) and
    the first (derivative-less) bucket are skipped. Returns {} aggs → all-zero on a
    degraded read, so the engine's guard holds state rather than false-firing.

    Return shape (keyed by ifIndex):
      { "3": {"rx_mbps": {"avg":.., "max":..}, "tx_mbps": {...},
              "utilization_pct": {...}, "oper_status": 1, "label": "WAN LinkNet"}, ... }
    """
    source_ip = SITE_SOURCE_MAP.get(site_name)
    if not source_ip:
        raise ValueError(f"Unknown site_name: {site_name}")
    iface_map = SITE_IFINDEX_MAP.get(site_name, {})
    if not iface_map:
        raise ValueError(f"No interface mapping for site: {site_name}")
    if_indexes = list(iface_map.keys())
    if client is None:
        client = _get_client_for_site(site_name)
    secs = _parse_interval_seconds(bucket)

    body = {
        "size": 0,
        "query": {"bool": {"filter": [
            _time_range(gte_ms, lte_ms),
            {"term": {"measurement_name.keyword": "fgt_iface_stats"}},
            {"term": {"tag.source.keyword": source_ip}},
            {"terms": {"tag.ifIndex.keyword": if_indexes}},
        ]}},
        "aggs": {"by_interface": {
            "terms": {"field": "tag.ifIndex.keyword", "size": len(if_indexes)},
            "aggs": {
                "speed": {"max": {"field": "fgt_iface_stats.ifHighSpeed_Mbps"}},
                "oper": {"max": {"field": "fgt_iface_stats.ifOperStatus"}},
                # min_doc_count=0 is MANDATORY: derivative pipeline aggs (in_d/out_d)
                # require it — min_doc_count=1 makes OpenSearch reject the query with
                # 400 "parent histogram of derivative aggregation must have min_doc_count
                # of 0", which safe_search swallows → empty result → every interface
                # rule reads as 0.
                "by_time": {
                    "date_histogram": {"field": "@timestamp", "fixed_interval": bucket, "min_doc_count": 0},
                    "aggs": {
                        "in_oct": {"max": {"field": "fgt_iface_stats.ifHCInOctets"}},
                        "out_oct": {"max": {"field": "fgt_iface_stats.ifHCOutOctets"}},
                        "in_d": {"derivative": {"buckets_path": "in_oct"}},
                        "out_d": {"derivative": {"buckets_path": "out_oct"}},
                    },
                },
            },
        }},
    }
    resp = await safe_search(client, INDEX_PATTERN, body)

    out: dict = {}
    for b in resp.get("aggregations", {}).get("by_interface", {}).get("buckets", []):
        speed = b.get("speed", {}).get("value") or 0.0
        rx, tx, util, thr = [], [], [], []
        for tb in b.get("by_time", {}).get("buckets", []):
            di = tb.get("in_d", {}).get("value")
            do = tb.get("out_d", {}).get("value")
            if di is None or do is None:   # first bucket has no derivative
                continue
            if di < 0 or do < 0:           # counter reset (reboot) → skip bucket
                continue
            rx_mbps = di * 8 / secs / 1e6
            tx_mbps = do * 8 / secs / 1e6
            rx.append(rx_mbps)
            tx.append(tx_mbps)
            thr.append(max(rx_mbps, tx_mbps))   # busier direction, absolute Mbps
            if speed > 0:
                util.append(max(rx_mbps, tx_mbps) / speed * 100)
        stat = lambda v: {"avg": (sum(v) / len(v) if v else 0.0), "max": (max(v) if v else 0.0)}
        out[b["key"]] = {
            "rx_mbps": stat(rx), "tx_mbps": stat(tx), "utilization_pct": stat(util),
            "throughput_mbps": stat(thr),   # busier direction; absolute-Mbps + %-of-link-max alerts
            "oper_status": b.get("oper", {}).get("value"),
            "label": iface_map.get(b["key"], b["key"]),
        }
    return out
