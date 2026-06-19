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
    client: Optional[AsyncOpenSearch] = None,
) -> dict:
    """
    Fetch per-interface stats timeline from OpenSearch.

    Only queries the 4 hardcoded WAN/MPLS interfaces per site
    defined in SITE_IFINDEX_MAP. No dynamic discovery.
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
                            "fixed_interval": "60s",
                            "min_doc_count": 0,
                            "extended_bounds": {
                                "min": gte_ms,
                                "max": lte_ms,
                            },
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

    resp = await client.search(index=INDEX_PATTERN, body=body)
    return resp["aggregations"]
