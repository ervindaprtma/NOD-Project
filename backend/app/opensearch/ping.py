"""OpenSearch query builders for the Ping Monitor (plans/2026-10-21_ping-monitor.md §3.1).

telegraf writes `measurement_name="ping"` into telegraf-index* on both clusters (DC and
DRC, ~30 s poll). Identity is `tag.url` (IP or DNS name) per cluster; `tag.target_name`
is a collector-side label used as the default display name. Metrics live under `ping.*`.
On a failed probe (result_code=1, 100% loss) the latency fields are ABSENT, so avg/min/max
aggregations skip them naturally and charts show gaps.

Q-rules: Q-01 @timestamp range on every query, Q-02 explicit terms size (≤ 500),
Q-03 _source includes only (top_hits), Q-04 no scroll, Q-05 aggregations only,
Q-06 exact term on measurement_name.keyword, Q-07 one query per cluster (issued
concurrently), never one per target.

An unreachable cluster (e.g. DC under maintenance) is handled by safe_search: it returns
an empty degraded skeleton, so the other cluster's targets still render.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import drop_partial_tail, safe_search

INDEX_PATTERN = "telegraf-index*"
MEASUREMENT = "ping"
CLUSTERS: dict[str, Callable[[], AsyncOpenSearch]] = {"dc": get_dc_client, "drc": get_drc_client}
# Alert rules carry the canonical site in site_name; ping targets are per cluster.
SITE_CLUSTER: dict[str, str] = {"Site_FGT-DC": "dc", "Site_FGT-DRC": "drc"}

_URL_SIZE = 500  # Q-02 cap; ~15 targets per cluster today

# Current-state window for the target list / KPIs: long enough to average ~30 probes,
# short enough that status reflects "now".
LIVE_WINDOW_MS: int = 15 * 60 * 1000

# Chart bucket ladder (span seconds → bucket seconds). Never below 60 s: the poll is
# ~30 s and a 1 m floor keeps every bucket populated on a healthy target.
_BUCKET_LADDER = ((7_200, 60), (21_600, 180), (43_200, 300), (86_400, 600))


def live_window(now_ms: Optional[int] = None) -> tuple[int, int]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return now - LIVE_WINDOW_MS, now


def bucket_seconds(gte_ms: int, lte_ms: int) -> int:
    span = (lte_ms - gte_ms) / 1000
    for limit, secs in _BUCKET_LADDER:
        if span <= limit:
            return secs
    return max(3_600, int(span / 300) // 60 * 60)  # ≤ ~300 buckets on long custom ranges


def _query(gte_ms: int, lte_ms: int, *extra: dict) -> dict:
    """Bounded ping query. result_code 2 = the collector itself failed (e.g. a second,
    untagged telegraf that can't resolve DNS) — those docs carry no metrics and say
    nothing about the target, so they are excluded everywhere. 0 = reply, 1 = no reply."""
    return {"bool": {
        "filter": [
            {"term": {"measurement_name.keyword": MEASUREMENT}},
            {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            *extra,
        ],
        "must_not": [{"term": {"ping.result_code": 2}}],
    }}


def _metric_aggs() -> dict:
    """Per-target window aggregates shared by the live list and the detail stats."""
    return {
        "avg_ms": {"avg": {"field": "ping.average_response_ms"}},
        "min_ms": {"min": {"field": "ping.minimum_response_ms"}},
        "max_ms": {"max": {"field": "ping.maximum_response_ms"}},
        "jitter_ms": {"avg": {"field": "ping.standard_deviation_ms"}},
        "loss_pct": {"avg": {"field": "ping.percent_packet_loss"}},
        "max_loss_pct": {"max": {"field": "ping.percent_packet_loss"}},
        "ok": {"filter": {"term": {"ping.result_code": 0}},
               "aggs": {"last": {"max": {"field": "@timestamp"}}}},
    }


def discover_body(gte_ms: int, lte_ms: int) -> dict:
    return {
        "size": 0,
        "query": _query(gte_ms, lte_ms),
        "aggs": {"by_url": {
            "terms": {"field": "tag.url.keyword", "size": _URL_SIZE},
            "aggs": {
                "last_seen": {"max": {"field": "@timestamp"}},
                "name": {"terms": {"field": "tag.target_name.keyword", "size": 1}},
            },
        }},
    }


def live_body(urls: list[str], gte_ms: int, lte_ms: int) -> dict:
    return {
        "size": 0,
        "query": _query(gte_ms, lte_ms, {"terms": {"tag.url.keyword": urls}}),
        "aggs": {"by_url": {
            "terms": {"field": "tag.url.keyword", "size": min(len(urls), _URL_SIZE) or 1},
            "aggs": {
                **_metric_aggs(),
                "last_seen": {"max": {"field": "@timestamp"}},
                "name": {"terms": {"field": "tag.target_name.keyword", "size": 1}},
                "latest": {"top_hits": {
                    "size": 1,
                    "sort": [{"@timestamp": {"order": "desc"}}],
                    "_source": {"includes": ["ping.result_code", "ping.percent_packet_loss"]},
                }},
            },
        }},
    }


def series_body(url: str, gte_ms: int, lte_ms: int, bucket_s: int) -> dict:
    return {
        "size": 0,
        "track_total_hits": True,  # exact sample count → availability % on long ranges
        "query": _query(gte_ms, lte_ms, {"term": {"tag.url.keyword": url}}),
        "aggs": {
            **_metric_aggs(),
            "over_time": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": f"{bucket_s}s",
                    "min_doc_count": 0,
                    "extended_bounds": {"min": gte_ms, "max": lte_ms},
                },
                "aggs": {
                    "avg_ms": {"avg": {"field": "ping.average_response_ms"}},
                    "max_ms": {"max": {"field": "ping.maximum_response_ms"}},
                    "jitter_ms": {"avg": {"field": "ping.standard_deviation_ms"}},
                    "loss_pct": {"avg": {"field": "ping.percent_packet_loss"}},
                },
            },
        },
    }


def _val(agg: Optional[dict]) -> Optional[float]:
    v = (agg or {}).get("value")
    return float(v) if v is not None else None


def _ping_field(src: dict, key: str) -> Any:
    """telegraf _source may be nested ({"ping": {...}}) or flat ("ping.key")."""
    nested = src.get("ping")
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    return src.get(f"ping.{key}")


def _parse_metrics(b: dict) -> dict:
    samples = int(b.get("doc_count", 0))
    ok_agg = b.get("ok") or {}
    ok = int(ok_agg.get("doc_count", 0))
    last_ok = _val(ok_agg.get("last"))
    return {
        "avg_ms": _val(b.get("avg_ms")),
        "min_ms": _val(b.get("min_ms")),
        "max_ms": _val(b.get("max_ms")),
        "jitter_ms": _val(b.get("jitter_ms")),
        "loss_pct": _val(b.get("loss_pct")),
        "max_loss_pct": _val(b.get("max_loss_pct")),
        "samples": samples,
        "availability_pct": round(ok / samples * 100, 2) if samples else None,
        "last_ok_ms": int(last_ok) if last_ok is not None else None,
    }


def _first_key(agg: Optional[dict]) -> Optional[str]:
    buckets = (agg or {}).get("buckets") or []
    return str(buckets[0]["key"]) if buckets else None


def parse_live_bucket(b: dict) -> dict:
    hits = ((b.get("latest") or {}).get("hits") or {}).get("hits") or []
    src = hits[0].get("_source", {}) if hits else {}
    rc = _ping_field(src, "result_code")
    loss = _ping_field(src, "percent_packet_loss")
    last = _val(b.get("last_seen"))
    return {
        **_parse_metrics(b),
        "target_name": _first_key(b.get("name")),
        "last_seen_ms": int(last) if last is not None else None,
        "latest_result_code": int(rc) if rc is not None else None,
        "latest_loss_pct": float(loss) if loss is not None else None,
    }


async def discover_ping_urls(gte_ms: int, lte_ms: int) -> list[dict]:
    """Distinct ping urls per cluster (powers the Add-target picker)."""
    names = list(CLUSTERS)
    results = await asyncio.gather(*(
        safe_search(CLUSTERS[c](), INDEX_PATTERN, discover_body(gte_ms, lte_ms)) for c in names
    ))
    out: list[dict] = []
    for cluster, resp in zip(names, results):
        for b in resp.get("aggregations", {}).get("by_url", {}).get("buckets", []):
            last = _val(b.get("last_seen"))
            out.append({
                "cluster": cluster,
                "url": str(b["key"]),
                "target_name": _first_key(b.get("name")),
                "last_seen_ms": int(last) if last is not None else None,
            })
    return out


async def ping_live(targets: dict[str, list[str]], gte_ms: int, lte_ms: int) -> dict[tuple[str, str], dict]:
    """Window metrics + latest probe for curated targets, keyed by (cluster, url).

    `targets` maps cluster → urls. One query per cluster that has targets (Q-07).
    A target with no docs in the window is simply absent from the result.
    """
    jobs = [(c, urls) for c, urls in targets.items() if urls and c in CLUSTERS]
    results = await asyncio.gather(*(
        safe_search(CLUSTERS[c](), INDEX_PATTERN, live_body(urls, gte_ms, lte_ms)) for c, urls in jobs
    ))
    out: dict[tuple[str, str], dict] = {}
    for (cluster, _urls), resp in zip(jobs, results):
        for b in resp.get("aggregations", {}).get("by_url", {}).get("buckets", []):
            out[(cluster, str(b["key"]))] = parse_live_bucket(b)
    return out


async def ping_series(cluster: str, url: str, gte_ms: int, lte_ms: int) -> dict:
    """Time series + window stats for one target (detail charts and stat tiles)."""
    bucket_s = bucket_seconds(gte_ms, lte_ms)
    resp = await safe_search(CLUSTERS[cluster](), INDEX_PATTERN, series_body(url, gte_ms, lte_ms, bucket_s))
    aggs = resp.get("aggregations", {}) or {}
    buckets = drop_partial_tail(aggs.get("over_time", {}).get("buckets", []), bucket_s, lte_ms)
    points = [{
        "ts_ms": int(b["key"]),
        "avg_ms": _val(b.get("avg_ms")),
        "max_ms": _val(b.get("max_ms")),
        "jitter_ms": _val(b.get("jitter_ms")),
        "loss_pct": _val(b.get("loss_pct")),
    } for b in buckets]
    stats = _parse_metrics({**aggs, "doc_count": resp.get("hits", {}).get("total", {}).get("value", 0)})
    return {"bucket_seconds": bucket_s, "points": points, "stats": stats}


def derive_status(enabled: bool, expected_down: bool, m: Optional[dict],
                  warn_loss_pct: float, warn_latency_ms: float) -> str:
    """Server-side status (plan §3.2): disabled / no_data / down / expected_down / degraded / up."""
    if not enabled:
        return "disabled"
    if not m or not m.get("samples"):
        return "expected_down" if expected_down else "no_data"
    latest_failed = m.get("latest_result_code") == 1 or (m.get("latest_loss_pct") or 0) >= 100
    if latest_failed:
        return "expected_down" if expected_down else "down"
    loss, avg = m.get("loss_pct"), m.get("avg_ms")
    if (loss is not None and loss > warn_loss_pct) or (avg is not None and avg > warn_latency_ms):
        return "degraded"
    return "up"
