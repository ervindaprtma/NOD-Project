"""Site-migration detection for device_uptime (Resources → Availability).

A device may be re-tagged in Telegraf (tag.site DC → office). Identity stays
tag.source (IP); placement changes. This module answers one question per
window: which devices moved into/out of a site recently enough that their
OLD tag's docs sit inside the queried range?

Pure helpers here are cluster-free and unit-testable; the site-map cache is a
module-level dict with a TTL — rebuilt lazily per OpenSearch endpoint.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

MAP_TTL_SECONDS: int = 15 * 60          # tag pairs drift weekly, not per-minute
MAP_MAX_ENTRIES: int = 5_000            # ponytail: fleet-scale cap; evict-all rebuild beyond

# Map cache keyed by id(client)-independent endpoint hint ("dc"/"drc").
_map_cache: Dict[str, Tuple[float, Dict[str, Dict[str, int]]]] = {}


def norm_site(tag_value: Optional[str]) -> str:
    """Normalized site tag for comparisons ONLY ('DC' ≡ 'dc'); never stored back."""
    return (tag_value or "").strip().lower()


def build_site_map_query(window_ms: int) -> dict:
    """Size-0 agg: every (ip → site_tag → last seen) pair over the given span.

    Q-02: explicit size ≤ 1000. Q-01: @timestamp bounds present. Q-06 spirit:
    measurement_name pinned exactly; no wildcard on tag values.
    """
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"measurement_name.keyword": "device_uptime"}},
                    {"range": {"@timestamp": {"gte": "now-" + _human_ms(window_ms),
                                              "lte": "now", "format": "epoch_millis"}}},
                ]
            }
        },
        "aggs": {
            "by_ip": {
                # UI cap; a fleet larger than this collapses tail devices.
                "terms": {"field": "tag.source.keyword", "size": 1000},
                "aggs": {
                    "sites": {
                        "terms": {"field": "tag.site.keyword", "size": 10},
                        "aggs": {"last": {"max": {"field": "@timestamp"}}}
                    }
                },
            }
        },
    }


def _human_ms(ms: int) -> str:
    days = max(1, round(ms / 86_400_000))
    return f"{days}d"


async def fetch_site_map(client: Any, endpoint_key: str, window_ms: int,
                         safe_search_fn) -> Dict[str, Dict[str, int]]:
    """Return {ip: {normalized_site_tag: last_doc_ms}} (15-min cached per endpoint).

    safe_search_fn is injected (query.safe_search) to avoid a circular import.
    Raises on failure — caller decides policy (skip stitch quietly).
    """
    now = time.monotonic()
    hit = _map_cache.get(endpoint_key)
    if hit and now - hit[0] < MAP_TTL_SECONDS:
        return hit[1]

    from app.opensearch.device_uptime import INDEX_PATTERN
    resp = await safe_search_fn(client, INDEX_PATTERN, build_site_map_query(window_ms))
    out: Dict[str, Dict[str, int]] = {}
    for bucket in ((resp.get("aggregations") or {}).get("by_ip", {}) or {}).get("buckets", []):
        ip = bucket["key"]
        eras: Dict[str, int] = {}
        for s in bucket.get("sites", {}).get("buckets", []):
            ts = int(s["last"]["value"] or 0)
            if ts:
                eras[norm_site(s["key"])] = ts
        if eras:
            out[ip] = eras

    if len(out) > MAP_MAX_ENTRIES:      # ponytail: blunt cap, rebuild next TTL cycle
        out.clear()

    _map_cache[endpoint_key] = (now, out)
    return out


def detect_moved_in(
    site_map: Dict[str, Dict[str, int]],
    roster_ips: List[str],
    site_tag: str,
    gte_ms: int,
    lte_ms: int,
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Classify roster devices against their multi-era site history.

    Returns (moved_in, moved_away):
      moved_in   {ip: {"moved_from": norm_old_tag, "moved_at_ms": boundary_ts}}
                 — device's CURRENT home is this site (queried tag era exists and
                 is newer than the prior era). The prior era gets stitched.
      moved_away [ip, ...]
                 — device's newest era is under a DIFFERENT site (it moved OFF
                 this page). It must NOT be rostered/stitched here.

    Direction rule: compare the other-era timestamp against the queried tag's
    OWN era timestamp. Other-era OLDER → moved in here (stitch). Other-era
    NEWER → moved away (exclude). Equal/missing here-era → treat as moved in
    only if roster membership proves current reporting (map TTL can lag).
    """
    here = norm_site(site_tag)
    moved_in: Dict[str, Dict[str, Any]] = {}
    moved_away: List[str] = []
    for ip in roster_ips:
        eras = site_map.get(ip)
        if not eras:
            continue
        # Normalize a copy: casing drift ('DC' vs 'dc') must merge, never move.
        norm_eras: Dict[str, int] = {}
        for tag, ts in eras.items():
            k = norm_site(tag)
            # keep the NEWEST doc per normalized site
            if ts > norm_eras.get(k, 0):
                norm_eras[k] = ts
        if not norm_eras:
            continue

        here_ts = norm_eras.get(here)
        others = [(t, ts) for t, ts in norm_eras.items() if t != here]
        if not others:
            continue

        # Newest other-site era decides direction.
        newest_other_tag, newest_other_ts = max(others, key=lambda p: p[1])

        if here_ts is not None and newest_other_ts > here_ts:
            # Device's latest activity is under ANOTHER site → it moved away.
            moved_away.append(ip)
            continue

        # Prior era inside the window → moved IN here; stitch that era.
        candidates = [
            (t, ts) for t, ts in norm_eras.items()
            if t != here and gte_ms <= ts <= lte_ms
        ]
        if candidates:
            tag, ts = max(candidates, key=lambda p: p[1])
            moved_in[ip] = {"moved_from": tag, "moved_at_ms": ts}
    return moved_in, moved_away


def extract_roster_ips(aggregations: dict) -> List[str]:
    """IPs present in the by_device terms of an already-executed availability query."""
    return [
        b["key"]
        for b in (aggregations.get("by_device", {}) or {}).get("buckets", [])
    ]


def invalidate_cache(endpoint_key: Optional[str] = None) -> None:
    """Test hook / manual refresh."""
    if endpoint_key is None:
        _map_cache.clear()
    else:
        _map_cache.pop(endpoint_key, None)
