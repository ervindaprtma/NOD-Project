"""
OpenSearch query builders for telegraf-index* — HA & Resource domain.
Q-06: ALL queries include exact term filter on measurement_name.
Q-01: ALL queries include @timestamp range filter with gte/lte.
"""
from __future__ import annotations

import time
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client


def _ha_filters(gte_ms: int, lte_ms: int) -> list[dict]:
    """Q-01 + Q-06."""
    return [
        {
            "range": {
                "@timestamp": {
                    "gte": gte_ms,
                    "lte": lte_ms,
                    "format": "epoch_millis",
                }
            }
        },
        {"term": {"measurement_name.keyword": "ha_member"}},  # Q-06: exact
    ]


# ─────────────────────────────────────────────────────────────────
# Resource Timeline (FR-04: RES-02, RES-03, RES-04)
# ─────────────────────────────────────────────────────────────────


async def resource_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    interval: str = "5m",
) -> dict:
    """
    Q-07: single query with terms agg on tag.device + date_histogram sub-agg.
    Returns multi-device timeline for CPU, memory, sessions.
    """
    if client is None:
        client = get_dc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": _ha_filters(gte_ms, lte_ms)}
        },
        "aggs": {
            "by_device": {
                "terms": {
                    "field": "tag.hostname.keyword",
                    "size": 20,  # Q-02
                },
                "aggs": {
                    "timeline": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                        },
                        "aggs": {
                            "avg_cpu": {"avg": {"field": "ha_member.cpu_usage"}},
                            "avg_mem": {"avg": {"field": "ha_member.mem_usage"}},
                            "avg_sessions": {"avg": {"field": "ha_member.session_count"}},
                        },
                    }
                },
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    buckets = resp["aggregations"]["by_device"]["buckets"]

    cpu: list[dict] = []
    memory: list[dict] = []
    sessions: list[dict] = []

    for device_bucket in buckets:
        hostname = device_bucket["key"]
        for tb in device_bucket["timeline"]["buckets"]:
            ts = tb["key"]
            cpu.append({"timestamp": ts, "value": tb["avg_cpu"]["value"] or 0.0, "device": hostname})
            memory.append({"timestamp": ts, "value": tb["avg_mem"]["value"] or 0.0, "device": hostname})
            sessions.append({"timestamp": ts, "value": int(tb["avg_sessions"]["value"] or 0), "device": hostname})

    return {"cpu": cpu, "memory": memory, "sessions": sessions}


# ─────────────────────────────────────────────────────────────────
# Current Device Status (FR-04: RES-05, RES-06, RES-07)
# ─────────────────────────────────────────────────────────────────


async def current_device_status(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """
    Q-07: terms agg on tag.device with top_hits (size:1, sort @timestamp desc).
    Returns latest CPU, memory, sessions, sync_status per device.
    """
    if client is None:
        client = get_dc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": _ha_filters(gte_ms, lte_ms)}
        },
        "aggs": {
            "by_device": {
                "terms": {
                    "field": "tag.hostname.keyword",
                    "size": 20,  # Q-02
                },
                "aggs": {
                    "latest": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"@timestamp": {"order": "desc"}}],
                            "_source": {
                                "includes": [
                                    "ha_member.cpu_usage",
                                    "ha_member.mem_usage",
                                    "ha_member.session_count",
                                    "ha_member.sync_status",
                                    "tag.hostname",
                                    "tag.serial_number",
                                    "tag.device",
                                ]
                            },  # Q-03
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    buckets = resp["aggregations"]["by_device"]["buckets"]

    results = []
    for bucket in buckets:
        hits = bucket["latest"]["hits"]["hits"]
        if not hits:
            continue
        src = hits[0]["_source"]
        ha = src.get("ha_member", {})
        tag = src.get("tag", {})

        sync_val = ha.get("sync_status")
        sync_label = "In Sync" if sync_val == 1 else ("Out of Sync" if sync_val is not None else "Unknown")

        results.append({
            "device": tag.get("device", ""),
            "hostname": tag.get("hostname", bucket["key"]),
            "serial_number": tag.get("serial_number", ""),
            "cpu_usage": float(ha.get("cpu_usage", 0) or 0),
            "mem_usage": float(ha.get("mem_usage", 0) or 0),
            "session_count": int(ha.get("session_count", 0) or 0),
            "sync_status": sync_label,
        })

    return results


async def session_sparkline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """... (existing function, unchanged) ..."""
    if client is None:
        client = get_dc_client()

    # Calculate interval so we get ~15 data points
    delta_min = (lte_ms - gte_ms) / 60000
    if delta_min <= 15:
        interval = "1m"
    elif delta_min <= 60:
        interval = "5m"
    elif delta_min <= 360:
        interval = "15m"
    else:
        interval = "30m"

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ha_filters(gte_ms, lte_ms)}},
        "aggs": {
            "by_device": {
                "terms": {"field": "tag.hostname.keyword", "size": 20},
                "aggs": {
                    "timeline": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                        },
                        "aggs": {
                            "avg_sessions": {"avg": {"field": "ha_member.session_count"}}
                        },
                    }
                },
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    result: list[dict] = []
    for bucket in resp["aggregations"]["by_device"]["buckets"]:
        hostname = bucket["key"]
        points = [
            {"timestamp": b["key"], "value": b["avg_sessions"]["value"] or 0}
            for b in bucket["timeline"]["buckets"]
        ]
        result.append({"device": hostname, "points": points})
    return result


# ─────────────────────────────────────────────────────────────────
# HA Cluster Status (FR-04 extension: HA health & member state)
# ─────────────────────────────────────────────────────────────────


async def ha_cluster_status(site_name: str = "Site_FGT-DC") -> dict:
    """
    Query HA cluster status for a given site.
    Only Site_FGT-DC has HA configured (active-passive on telegraf cluster).
    For other sites, returns standalone/critical response.

    Queries telegraf-index* for last 5 min:
      - measurement_name = ha_member (per-member stats + sync_status)
      - measurement_name = Site_FGT-DC_HA (cluster config: ha_mode, priority, etc.)
      - tag.source = 10.80.150.1 (DC only)

    Returns:
      {
        "ha_mode": "active-passive" | "active-active" | "standalone",
        "members": [{memberIndex, role, syncStatus, priority, hostname}, ...],
        "overallHealth": "healthy" | "degraded" | "critical",
      }
    """
    if site_name != "Site_FGT-DC":
        return {
            "ha_mode": "standalone",
            "overallHealth": "critical",
            "message": "HA not configured for this site",
        }

    import time

    client = get_dc_client()
    now_ms = int(time.time() * 1000)
    five_min_ago = now_ms - 5 * 60 * 1000

    body = {
        "size": 50,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": five_min_ago,
                                "lte": now_ms,
                                "format": "epoch_millis",
                            }
                        }
                    },
                    {"terms": {"measurement_name.keyword": ["ha_member", "Site_FGT-DC_HA"]}},
                    {"term": {"tag.device.keyword": "FGT-DC"}},
                ],
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    resp = await client.search(index="telegraf-index*", body=body)
    hits = resp["hits"]["hits"]

    # Separate cluster config doc from per-member docs
    ha_config: dict = {}
    members_by_hostname: dict[str, dict] = {}  # hostname -> latest _source

    for hit in hits:
        src = hit["_source"]
        measurement = src.get("measurement_name", "")
        tag = src.get("tag", {})
        hostname = tag.get("hostname", "unknown")

        if measurement == "Site_FGT-DC_HA" and not ha_config:
            ha_config = src.get("Site_FGT-DC_HA", {})
        elif measurement == "ha_member" and hostname not in members_by_hostname:
            members_by_hostname[hostname] = src

    # Determine HA mode from cluster config
    ha_mode = "standalone"
    if ha_config:
        mode_int = ha_config.get("ha_mode", 0)
        if mode_int == 3:
            ha_mode = "active-passive"
        elif mode_int == 4:
            ha_mode = "active-active"
        elif mode_int == 5:
            ha_mode = "active-active"  # some FortiOS versions use 5 for A-A

    # Build members list
    members: list[dict] = []
    for idx, (hostname, src) in enumerate(members_by_hostname.items()):
        ha_member_data = src.get("ha_member", {})
        tag_data = src.get("tag", {})

        sync_val = ha_member_data.get("sync_status")
        sync_status = "in-sync" if sync_val == 1 else "out-of-sync"

        # Role: first member (by discovery order) is assumed active;
        # if hostname-based ordering is available, highest-priority member is active.
        # We default to idx==0 as active, others standby.
        role = "active" if idx == 0 else "standby"

        members.append({
            "memberIndex": idx,
            "role": role,
            "syncStatus": sync_status,
            "priority": ha_config.get("ha_priority", 0),
            "hostname": hostname,
        })

    # Compute overall health
    member_count = len(members)
    if ha_mode == "standalone" or member_count < 2:
        overall_health = "critical"
    elif all(m["syncStatus"] == "in-sync" for m in members):
        overall_health = "healthy"
    else:
        overall_health = "degraded"

    return {
        "ha_mode": ha_mode,
        "members": members,
        "overallHealth": overall_health,
    }


# ─────────────────────────────────────────────────────────────────
# Single-device resource queries (DRC & Office)
# ─────────────────────────────────────────────────────────────────

# Site → (client_factory, measurement_name)
SITE_RESOURCE_MAP: dict[str, tuple] = {
    "Site_FGT-DRC": (get_drc_client, "Resource_FGT-DRC"),
    "Site_FGT_Office": (get_dc_client, "Resource_FGT-Office"),
}


def _resource_filters(gte_ms: int, lte_ms: int, measurement_name: str) -> list[dict]:
    """Q-01 + Q-06 for Resource_FGT-* measurements."""
    return [
        {
            "range": {
                "@timestamp": {
                    "gte": gte_ms,
                    "lte": lte_ms,
                    "format": "epoch_millis",
                }
            }
        },
        {"term": {"measurement_name.keyword": measurement_name}},
    ]


async def resource_device_status(
    site_name: str,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> dict | None:
    """
    Get latest resource status for a single-device site (DRC/Office).
    Returns: {cpu_usage_percent, mem_usage_percent, mem_capacity_kb, serial_number, session_count, source_ip}
    """
    if site_name not in SITE_RESOURCE_MAP:
        return None

    client_factory, measurement = SITE_RESOURCE_MAP[site_name]
    client = client_factory()

    body = {
        "size": 1,
        "query": {
            "bool": {"filter": _resource_filters(gte_ms, lte_ms, measurement)}
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
        "_source": {
            "includes": [
                f"{measurement}.cpu_usage_percent",
                f"{measurement}.mem_usage_percent",
                f"{measurement}.mem_capacity_kb",
                f"{measurement}.serial_number",
                f"{measurement}.session_count",
                "tag.source",
                "@timestamp",
            ]
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    hits = resp["hits"]["hits"]
    if not hits:
        return None

    src = hits[0]["_source"]
    device_data = src.get(measurement, {})
    tag = src.get("tag", {})

    return {
        "site": site_name,
        "cpu_usage_percent": float(device_data.get("cpu_usage_percent", 0) or 0),
        "mem_usage_percent": float(device_data.get("mem_usage_percent", 0) or 0),
        "mem_capacity_kb": int(device_data.get("mem_capacity_kb", 0) or 0),
        "serial_number": device_data.get("serial_number", ""),
        "session_count": int(device_data.get("session_count", 0) or 0),
        "source_ip": tag.get("source", ""),
        "timestamp": src.get("@timestamp", ""),
    }


async def resource_device_timeline(
    site_name: str,
    gte_ms: int = 0,
    lte_ms: int = 0,
    interval: str = "5m",
) -> dict:
    """
    Get resource timeline for a single-device site (DRC/Office).
    Returns: {cpu: [...], memory: [...], sessions: [...]}
    """
    if site_name not in SITE_RESOURCE_MAP:
        return {"cpu": [], "memory": [], "sessions": []}

    client_factory, measurement = SITE_RESOURCE_MAP[site_name]
    client = client_factory()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": _resource_filters(gte_ms, lte_ms, measurement)}
        },
        "aggs": {
            "timeline": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval,
                    "min_doc_count": 0,
                },
                "aggs": {
                    "avg_cpu": {"avg": {"field": f"{measurement}.cpu_usage_percent"}},
                    "avg_mem": {"avg": {"field": f"{measurement}.mem_usage_percent"}},
                    "avg_sessions": {"avg": {"field": f"{measurement}.session_count"}},
                },
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    buckets = resp["aggregations"]["timeline"]["buckets"]

    cpu: list[dict] = []
    memory: list[dict] = []
    sessions: list[dict] = []

    for tb in buckets:
        ts = tb["key"]
        cpu.append({"timestamp": ts, "value": tb["avg_cpu"]["value"] or 0.0})
        memory.append({"timestamp": ts, "value": tb["avg_mem"]["value"] or 0.0})
        sessions.append({"timestamp": ts, "value": int(tb["avg_sessions"]["value"] or 0)})

    return {"cpu": cpu, "memory": memory, "sessions": sessions}
