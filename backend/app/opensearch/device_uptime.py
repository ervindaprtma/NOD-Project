"""
OpenSearch query builder for telegraf-index* — device_uptime domain.

Turns the SNMP `sys_uptime` counter into a per-device availability percentage.
Design: design_availability_devices.md (§ refs below point at it).

Q-01: @timestamp range filter on every query.
Q-02: terms agg has an explicit size.
Q-05: counting/aggregation happens in OpenSearch, not Python.
Q-06: exact term filter on measurement_name.keyword.
Q-07: ONE query per site — devices are a terms agg, not a loop.

The arithmetic lives in pure functions below the query so it is testable
without a cluster (see tests/test_device_uptime.py).
"""
from __future__ import annotations

import time
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search

INDEX_PATTERN: str = "telegraf-index*"
MEASUREMENT: str = "device_uptime"

# Telegraf scrapes every 30s — verified against live data (poll count x 30s
# matches the observed sys_uptime delta on every device).
POLL_INTERVAL_SECONDS: int = 30

# sys_uptime is SNMP TimeTicks: hundredths of a second, 32-bit.
TICKS_PER_SECOND: int = 100
WRAP_MAX: int = 4_294_967_295          # 2**32 - 1 ~= 497.1 days
WRAP_GUARD: int = 4_200_000_000        # ~486d: above this a decrease may be a wrap, not a reboot

# A device is "not reporting" once it misses two consecutive scrapes.
STALE_AFTER_MS: int = POLL_INTERVAL_SECONDS * 2 * 1000

# Below this many devices a site-wide silence is ambiguous, so we do not
# blame the collector (design §2d).
MIN_DEVICES_FOR_COLLECTOR_GAP: int = 2

# tag.site values are lowercase short form, NOT NOD's canonical site names.
SITE_TAG: dict[str, str] = {
    "Site_FGT-DC": "dc",
    "Site_FGT-DRC": "drc",
    "Site_FGT_Office": "office",
}

# Same telegraf routing as interface_stats: DC + Office -> dc cluster, DRC -> drc.
SITE_ENDPOINT: dict[str, str] = {
    "Site_FGT-DC": "dc",
    "Site_FGT-DRC": "drc",
    "Site_FGT_Office": "dc",
}

WINDOW_SECONDS: dict[str, int] = {
    "24h": 86_400,
    "7d": 604_800,
    "30d": 2_592_000,
    "90d": 7_776_000,
    "365d": 31_536_000,
}

# ~100-365 buckets per window: enough shape to see an outage, few enough to render.
WINDOW_BUCKET: dict[str, str] = {
    "24h": "15m",
    "7d": "1h",
    "30d": "6h",
    "90d": "12h",
    "365d": "1d",
}


def _get_client_for_site(site_name: str) -> AsyncOpenSearch:
    """Return the OpenSearch client for a site (telegraf routing)."""
    if SITE_ENDPOINT.get(site_name, "dc") == "drc":
        return get_drc_client()
    return get_dc_client()


def _interval_seconds(interval: str) -> int:
    """'15m' -> 900. Mirrors interface_stats._parse_interval_seconds plus days."""
    interval = interval.strip()
    unit, value = interval[-1], interval[:-1]
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86_400}.get(unit)
    if mult is None or not value.isdigit():
        return 900
    return int(value) * mult


# ─────────────────────────────────────────────────────────────────
# Query
# ─────────────────────────────────────────────────────────────────


def build_query(site_tag: str, gte_ms: int, lte_ms: int, interval: str) -> dict:
    """
    One query, all devices at a site.

    `min_doc_count: 0` is load-bearing: empty buckets ARE the outage signal, and
    collector-gap detection (§2d) needs to see them. Dropping them would erase
    exactly what the availability percentage measures.

    `fixed_interval` (never `calendar_interval`) — the interval values above are
    multiples, which calendar_interval rejects with a 400 (the R-03 regression).
    """
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"measurement_name.keyword": MEASUREMENT}},
                    {"term": {"tag.site.keyword": site_tag}},
                    {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms,
                                              "format": "epoch_millis"}}},
                ]
            }
        },
        "aggs": {
            "by_device": {
                # keyed on tag.source (the IP): hostnames get renamed, IPs do not.
                "terms": {"field": "tag.source.keyword", "size": 200},
                "aggs": {
                    "latest": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"@timestamp": {"order": "desc"}}],
                            "_source": ["device_uptime.sys_uptime", "tag.hostname",
                                        "tag.vendor", "tag.site"],
                        }
                    },
                    "polls": {"value_count": {"field": "device_uptime.sys_uptime"}},
                    "first": {"min": {"field": "@timestamp"}},
                    "last": {"max": {"field": "@timestamp"}},
                    "series": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                            # Without extended_bounds, empty buckets are only emitted
                            # BETWEEN the first and last doc. A collector that dies
                            # mid-window would then produce no buckets at all for the
                            # dead stretch — the outage would be invisible to both the
                            # chart and collector-gap detection. Force the full range.
                            "extended_bounds": {"min": gte_ms, "max": lte_ms},
                        },
                        "aggs": {"max_uptime": {"max": {"field": "device_uptime.sys_uptime"}}},
                    },
                },
            }
        },
    }


def resolve_range(
    window: str, gte_ms: Optional[int], lte_ms: Optional[int], now_ms: Optional[int] = None
) -> tuple[int, int, str]:
    """
    Explicit gte/lte (drag-to-zoom) wins over the named window; the bucket
    interval is re-derived from whichever span we end up with.
    """
    if gte_ms is not None and lte_ms is not None and lte_ms > gte_ms:
        return gte_ms, lte_ms, _bucket_for_span((lte_ms - gte_ms) // 1000)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    seconds = WINDOW_SECONDS.get(window, WINDOW_SECONDS["24h"])
    return now_ms - seconds * 1000, now_ms, WINDOW_BUCKET.get(window, WINDOW_BUCKET["24h"])


def _bucket_for_span(span_seconds: int) -> str:
    """Pick a bucket for an arbitrary (zoomed) span — same ladder as WINDOW_BUCKET."""
    for limit, interval in ((3_600, "1m"), (21_600, "5m"), (86_400, "15m"),
                            (604_800, "1h"), (2_592_000, "6h"), (7_776_000, "12h")):
        if span_seconds <= limit:
            return interval
    return "1d"


# ─────────────────────────────────────────────────────────────────
# Pure helpers — the arithmetic (design §2), testable without a cluster
# ─────────────────────────────────────────────────────────────────


def uptime_seconds(ticks: float) -> float:
    """SNMP TimeTicks -> seconds."""
    return ticks / TICKS_PER_SECOND


def format_uptime_long(seconds: float) -> str:
    """`7 days 10 hours 5 minutes` — zero units dropped, singular/plural correct."""
    total = int(seconds)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60
    parts = [
        f"{value} {name}{'s' if value != 1 else ''}"
        for value, name in ((days, "day"), (hours, "hour"), (minutes, "minute"))
        if value
    ]
    return " ".join(parts) or "less than a minute"


def format_uptime_short(seconds: float) -> str:
    """`135d 22h` — compact form for dense table cells."""
    total = int(seconds)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def scan_reboots(points: list[dict]) -> tuple[list[dict], int]:
    """
    A reboot is a DECREASE in the uptime counter (§2c).

    Downtime is the polling gap around the reset — conservative, per the
    reference doc: a reboot faster than one scrape still registers as ~1
    interval, which is the pessimistic direction you want for an SLA.

    A decrease from near 2**32 is a 32-bit WRAP, not a reboot (§8): it is
    recorded with a note and contributes no downtime, so a device that has been
    up ~497 days does not report a phantom outage.

    `points` are ascending {ts_ms, ticks}; ticks None (empty bucket) is skipped.
    Returns (events, total_downtime_seconds); wrap events carry a `note`.
    """
    events: list[dict] = []
    total_downtime = 0
    prev_ts: Optional[int] = None
    prev_ticks: Optional[float] = None

    for point in points:
        ticks = point.get("ticks")
        if ticks is None:
            continue
        if prev_ticks is not None and ticks < prev_ticks:
            if prev_ticks >= WRAP_GUARD:
                events.append({"at_ms": point["ts_ms"], "downtime_seconds": 0,
                               "note": "possible counter wrap"})
            else:
                # `is not None`, never truthiness: an epoch-ms of 0 is a real timestamp.
                gap_ms = point["ts_ms"] - prev_ts if prev_ts is not None else 0
                downtime = max(0, gap_ms // 1000)
                total_downtime += downtime
                events.append({"at_ms": point["ts_ms"], "downtime_seconds": downtime,
                               "note": None})
        prev_ts, prev_ticks = point["ts_ms"], ticks

    return events, total_downtime


def find_collector_gaps(
    polls_by_device: dict[str, dict[int, int]],
    first_seen_by_device: dict[str, Optional[int]],
    bucket_ms: int,
    min_devices: int = MIN_DEVICES_FOR_COLLECTOR_GAP,
) -> list[dict]:
    """
    Tell a Telegraf outage apart from a device outage (§2d).

    If EVERY device that should be reporting is silent in the same bucket, the
    collector lost connectivity — not all of them died at once. If one device is
    silent while its peers report, that device is genuinely unreachable.

    Only decides when at least `min_devices` were expected: with a single device
    the signal is ambiguous, so it stays a device-level gap.

    Returns merged ranges [{start_ms, end_ms, duration_seconds}].
    """
    timestamps = sorted({ts for buckets in polls_by_device.values() for ts in buckets})
    gap_buckets: list[int] = []

    for ts in timestamps:
        expected = [
            device for device, first_seen in first_seen_by_device.items()
            if first_seen is not None and first_seen <= ts
        ]
        if len(expected) < min_devices:
            continue
        if sum(polls_by_device.get(device, {}).get(ts, 0) for device in expected) == 0:
            gap_buckets.append(ts)

    # Merge consecutive buckets into ranges.
    ranges: list[dict] = []
    for ts in gap_buckets:
        if ranges and ts == ranges[-1]["end_ms"]:
            ranges[-1]["end_ms"] = ts + bucket_ms
        else:
            ranges.append({"start_ms": ts, "end_ms": ts + bucket_ms})
    for entry in ranges:
        entry["duration_seconds"] = (entry["end_ms"] - entry["start_ms"]) // 1000
    return ranges


def compute_availability(
    window_start_ms: int,
    window_end_ms: int,
    first_seen_ms: Optional[int],
    device_down_seconds: float = 0.0,
) -> Optional[float]:
    """
    Counter-based availability (§9, revised) — the SLA number.

    The SNMP `sys_uptime` counter is ground truth for whether the device was up.
    A Telegraf timeout or packet loss leaves it untouched — it keeps climbing
    across a missed poll — so a poll gap is NOT downtime. Only a counter *reset*
    (reboot) or a device that fell silent and never returned is real downtime;
    that total arrives here as `device_down_seconds`.

        availability = (effective_window − device_down) / effective_window

    The window starts at `first_seen` so a device onboarded mid-window is judged
    only over its own lifetime, not penalised for time before it existed — without
    the clamp the live switches read ~5% available while perfectly healthy
    (measured, not hypothetical — design C2).

    Returns None when the effective window is empty (no history) — "unknown",
    never a fabricated number.
    """
    effective_start = max(window_start_ms, first_seen_ms or window_start_ms)
    window_seconds = (window_end_ms - effective_start) / 1000
    if window_seconds <= 0:
        return None
    up = max(0.0, window_seconds - device_down_seconds)
    return round(up / window_seconds * 100, 2)


def bucket_expected_polls(
    bucket_start_ms: int,
    bucket_ms: int,
    gte_ms: int,
    lte_ms: int,
    first_seen_ms: Optional[int],
    poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
) -> float:
    """
    How many polls this bucket could have received — from the slice of it that is
    actually inside the window AND after the device started reporting.

    Dividing by the full interval instead makes every edge bucket lie: the trailing
    bucket is still in progress, and the leading one starts mid-way, so both show a
    fake dip on an otherwise healthy device (measured: 41-70% at the edges).
    Returns 0 for a bucket the device could not have reported in at all.
    """
    start = max(bucket_start_ms, gte_ms, first_seen_ms if first_seen_ms is not None else gte_ms)
    end = min(bucket_start_ms + bucket_ms, lte_ms)
    covered_seconds = max(0, end - start) / 1000
    return covered_seconds / poll_interval_seconds


def _status(
    last_seen_ms: Optional[int], window_end_ms: int, reboots: list[dict], in_collector_gap: bool
) -> str:
    if in_collector_gap:
        return "collector_gap"
    if last_seen_ms is None or last_seen_ms < window_end_ms - STALE_AFTER_MS:
        return "not_reporting"
    if any(event["note"] is None for event in reboots):
        return "rebooted"
    return "up"


def _series_point(
    point: dict,
    bucket_ms: int,
    gte_ms: int,
    lte_ms: int,
    first_seen_ms: Optional[int],
    reboots: list[dict],
    down_bucket_ts: set,
    gap_bucket_ts: set,
) -> dict:
    """One chart bucket. Counter-based, consistent with the summary %: a bucket is
    100% up unless it falls in a reboot gap or a never-returned outage (0%).
    `availability_pct` is None where the device could not have reported at all
    (before it existed / outside the window) or during a collector gap (Telegraf
    down → unknown, not a device outage) — the chart breaks the line there instead
    of drawing a misleading 0%. `expected` (poll density) is kept for the tooltip."""
    ts = point["ts_ms"]
    start = max(ts, gte_ms, first_seen_ms if first_seen_ms is not None else gte_ms)
    covered = min(ts + bucket_ms, lte_ms) - start
    if covered <= 0 or ts in gap_bucket_ts:
        availability = None
    elif ts in down_bucket_ts:
        availability = 0.0
    else:
        availability = 100.0
    return {
        "ts_ms": ts,
        "uptime_seconds": uptime_seconds(point["ticks"]) if point["ticks"] is not None else None,
        "polls": point["polls"],
        "expected": int(round(bucket_expected_polls(ts, bucket_ms, gte_ms, lte_ms, first_seen_ms))),
        "availability_pct": availability,
        "reboot": any(event["at_ms"] == ts and event["note"] is None for event in reboots),
        "collector_gap": ts in gap_bucket_ts,
    }


def shape_result(
    aggregations: dict,
    gte_ms: int,
    lte_ms: int,
    bucket_seconds: int,
    site_tag: str,
    window: str,
) -> dict:
    """
    Turn raw aggregations into the API payload (design §3).

    Pure: every number here is reproducible from the aggregation dict, which is
    what makes the whole calculation testable without a cluster.
    """
    device_buckets = aggregations.get("by_device", {}).get("buckets", [])
    bucket_ms = bucket_seconds * 1000

    # Pass 1: per-device series + the cross-device view collector-gap needs.
    polls_by_device: dict[str, dict[int, int]] = {}
    first_seen_by_device: dict[str, Optional[int]] = {}
    raw: list[dict] = []

    for bucket in device_buckets:
        key = bucket["key"]
        hits = bucket.get("latest", {}).get("hits", {}).get("hits", [])
        source = hits[0].get("_source", {}) if hits else {}
        tags = source.get("tag", {}) or {}
        first = bucket.get("first", {}).get("value")
        last = bucket.get("last", {}).get("value")

        series = [
            {"ts_ms": int(point["key"]),
             "polls": int(point.get("doc_count", 0)),
             "ticks": point.get("max_uptime", {}).get("value")}
            for point in bucket.get("series", {}).get("buckets", [])
        ]
        polls_by_device[key] = {point["ts_ms"]: point["polls"] for point in series}
        first_seen_by_device[key] = int(first) if first is not None else None

        raw.append({
            "key": key,
            "hostname": tags.get("hostname") or key,
            "vendor": tags.get("vendor") or "",
            "site": tags.get("site") or site_tag,
            "ticks": (source.get("device_uptime") or {}).get("sys_uptime"),
            "polls": int(bucket.get("polls", {}).get("value") or 0),
            "first_seen_ms": int(first) if first is not None else None,
            "last_seen_ms": int(last) if last is not None else None,
            "series": series,
        })

    # Pass 2: collector gaps are a cross-device fact, so they need every device first.
    gaps = find_collector_gaps(polls_by_device, first_seen_by_device, bucket_ms)
    gap_seconds = sum(gap["duration_seconds"] for gap in gaps)
    gap_bucket_ts = {
        ts for gap in gaps
        for ts in range(gap["start_ms"], gap["end_ms"], bucket_ms)
    }

    devices: list[dict] = []
    for item in raw:
        ticks = item["ticks"]
        seconds = uptime_seconds(ticks) if ticks is not None else 0.0
        first_seen = item["first_seen_ms"]
        last_seen = item["last_seen_ms"]
        reboots, reboot_downtime = scan_reboots(item["series"])

        # Trailing outage: the device fell silent and never returned within the
        # window — real downtime, UNLESS the silence overlaps a site-wide collector
        # gap (Telegraf's fault), which we do not charge to the device.
        trailing_down = 0.0
        if last_seen is not None and last_seen < lte_ms - STALE_AFTER_MS:
            silent = (lte_ms - last_seen) / 1000
            tail_gap = sum(
                max(0, min(gap["end_ms"], lte_ms) - max(gap["start_ms"], last_seen)) / 1000
                for gap in gaps
            )
            trailing_down = max(0.0, silent - tail_gap)
        device_down = reboot_downtime + trailing_down

        availability = compute_availability(gte_ms, lte_ms, first_seen, device_down)
        effective_start = max(gte_ms, first_seen if first_seen is not None else gte_ms)
        window_seconds = max(0.0, (lte_ms - effective_start) / 1000)
        expected = int(round(window_seconds / POLL_INTERVAL_SECONDS))

        # Buckets to paint as an outage (0%) on the chart, kept consistent with the
        # summary %: reboot gaps + the non-collector-gap silent tail.
        down_bucket_ts: set[int] = set()
        for event in reboots:
            if event["note"] is not None:
                continue
            lo = event["at_ms"] - event["downtime_seconds"] * 1000
            down_bucket_ts.update(
                p["ts_ms"] for p in item["series"] if lo <= p["ts_ms"] < event["at_ms"]
            )
        if last_seen is not None and last_seen < lte_ms - STALE_AFTER_MS:
            down_bucket_ts.update(
                p["ts_ms"] for p in item["series"]
                if p["ts_ms"] > last_seen and p["ts_ms"] not in gap_bucket_ts
            )

        in_gap = bool(gap_bucket_ts) and last_seen is not None and any(
            ts > last_seen for ts in gap_bucket_ts
        )

        devices.append({
            "device_key": item["key"],
            "hostname": item["hostname"],
            "vendor": item["vendor"],
            "site": item["site"],
            "status": _status(item["last_seen_ms"], lte_ms, reboots, in_gap),
            "sys_uptime_ticks": int(ticks) if ticks is not None else 0,
            "uptime_seconds": seconds,
            "uptime_human_long": format_uptime_long(seconds),
            "uptime_human_short": format_uptime_short(seconds),
            "boot_time_ms": int(
                (item["last_seen_ms"] if item["last_seen_ms"] is not None else lte_ms)
                - seconds * 1000
            ),
            "first_seen_ms": item["first_seen_ms"],
            "last_seen_ms": item["last_seen_ms"],
            "partial_history": (
                item["first_seen_ms"] is not None and item["first_seen_ms"] > gte_ms
            ),
            "wrap_risk": ticks is not None and ticks >= WRAP_GUARD,
            "availability_pct": availability,
            "expected_polls": expected,
            "successful_polls": item["polls"],
            "excluded_collector_seconds": gap_seconds,
            "reboots": reboots,
            "reboot_count": sum(1 for event in reboots if event["note"] is None),
            "total_downtime_seconds": int(round(device_down)),
            "series": [
                _series_point(point, bucket_ms, gte_ms, lte_ms,
                              first_seen, reboots, down_bucket_ts, gap_bucket_ts)
                for point in item["series"]
            ],
        })

    devices.sort(key=lambda device: device["hostname"])
    # "Lowest" surfaces the most recently booted device — the smallest uptime, i.e.
    # the one most likely to have just rebooted. Not lowest availability.
    lowest = min(devices, key=lambda d: d["uptime_seconds"]) if devices else None
    history_start = min(
        (d["first_seen_ms"] for d in devices if d["first_seen_ms"] is not None), default=None
    )

    return {
        "summary": {
            "window": window,
            "window_seconds": (lte_ms - gte_ms) // 1000,
            "site": site_tag,
            "devices_total": len(devices),
            "devices_reporting": sum(1 for d in devices if d["status"] != "not_reporting"),
            "devices_partial_history": sum(1 for d in devices if d["partial_history"]),
            "devices_with_reboots": sum(1 for d in devices if d["reboot_count"]),
            # Per design §2b there is deliberately no fleet-average percentage:
            # averaging a 2h-old switch against a 50h firewall describes nothing.
            "lowest_uptime_device": ({"hostname": lowest["hostname"],
                                      "uptime_seconds": lowest["uptime_seconds"],
                                      "uptime_human_short": lowest["uptime_human_short"]}
                                     if lowest else None),
            "reboots_total": sum(d["reboot_count"] for d in devices),
            "collector_gap_seconds": gap_seconds,
            "collector_gaps": gaps,
            "history_start_ms": history_start,
            "history_sufficient": history_start is not None and history_start <= gte_ms,
        },
        "devices": devices,
    }


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────


async def device_availability(
    site_name: str = "Site_FGT-DC",
    window: str = "24h",
    gte_ms: Optional[int] = None,
    lte_ms: Optional[int] = None,
    client: Optional[AsyncOpenSearch] = None,
    now_ms: Optional[int] = None,
) -> dict[str, Any]:
    """
    Per-device availability for one site (design §3).

    `window` names the reporting range; explicit gte_ms/lte_ms override it, which
    is how drag-to-zoom asks for a sub-range.
    """
    site_tag = SITE_TAG.get(site_name)
    if site_tag is None:
        return {"summary": {}, "devices": []}

    start_ms, end_ms, interval = resolve_range(window, gte_ms, lte_ms, now_ms)
    if client is None:
        client = _get_client_for_site(site_name)

    response = await safe_search(
        client, INDEX_PATTERN, build_query(site_tag, start_ms, end_ms, interval)
    )
    return shape_result(
        response.get("aggregations", {}) or {},
        start_ms, end_ms, _interval_seconds(interval), site_tag, window,
    )
