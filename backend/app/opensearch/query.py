"""
OpenSearch query helpers with timeout, caching, and per-endpoint rate limiting.

Provides safe_search() wrapper that:
- Wraps OpenSearch queries with asyncio.wait_for timeout
- Implements lightweight in-memory caching for expensive aggregations
- Returns skeleton responses on timeout/error instead of raising
- Per-client cache namespacing to prevent cross-endpoint data leakage
- Per-client semaphore to prevent overwhelming the cluster

This prevents the backend from hitting OpenSearch circuit breakers
when multiple parallel queries (12 for Overview, 4 per traffic page)
hit the same cluster simultaneously.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
import logging
from typing import Any, Callable, Iterator, Optional

from opensearchpy import AsyncOpenSearch

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Degradation tracking ─────────────────────────────────────────
# safe_search() deliberately never raises: it returns an empty skeleton so one bad
# query can't 500 a whole page. The cost is that a failed query is indistinguishable
# from "no traffic" — the UI renders a confident 0 B. Endpoints call
# track_degradation() to get a sink; every skeleton/partial result below records why
# it degraded, and the endpoint reports it via Meta so the UI can say
# "data unavailable" instead of lying with a zero.
#
# The sink is a mutable list held in a ContextVar: asyncio child tasks inherit a copy
# of the context, but the list object itself is shared, so appends made inside
# concurrently-gathered queries are visible to the endpoint that created it. Requests
# never see each other's sinks.
_degraded_sink: ContextVar[Optional[list[str]]] = ContextVar("nod_degraded_sink", default=None)


def track_degradation() -> list[str]:
    """Start collecting degradation reasons for the current request context."""
    sink: list[str] = []
    _degraded_sink.set(sink)
    return sink


@contextmanager
def degradation_scope() -> Iterator[list[str]]:
    """Collect degradation reasons for one nested block, then restore the outer sink.

    Endpoints call track_degradation() once and let it cover the whole request. The
    alert engine instead needs per-query isolation — it must decide rule-by-rule
    whether the data is trustworthy — and must not clobber a request's sink if it ever
    runs inside one. Scoping restores the previous sink on exit.
    """
    sink: list[str] = []
    token = _degraded_sink.set(sink)
    try:
        yield sink
    finally:
        _degraded_sink.reset(token)


def _record_degraded(reason: str) -> None:
    sink = _degraded_sink.get()
    if sink is not None and len(sink) < 10:
        sink.append(reason)

# Per-client cache namespacing (keyed by client id(hosts))
_client_caches: dict[str, dict[str, tuple[float, Any]]] = {}
# Per-client semaphore to limit concurrent queries to a given cluster
# DC cluster: 2 concurrent (prevent circuit breaker on large aggs)
# DRC cluster: 4 concurrent (lighter data, more headroom)
_client_semaphores: dict[str, asyncio.Semaphore] = {}
_DEFAULT_DC_CONCURRENCY = 2
_DEFAULT_DRC_CONCURRENCY = 4

_CACHE_TTL_SECONDS = 30
_CACHE_MAX_ENTRIES = 256


def _client_id(client: AsyncOpenSearch) -> str:
    try:
        return json.dumps(client.transport.hosts, sort_keys=True)
    except Exception:
        return str(id(client))


def _get_cache(client: AsyncOpenSearch) -> dict[str, tuple[float, Any]]:
    cid = _client_id(client)
    if cid not in _client_caches:
        _client_caches[cid] = {}
    return _client_caches[cid]


def _get_semaphore(client: AsyncOpenSearch) -> asyncio.Semaphore:
    """Get a semaphore for a client, with DC getting lower concurrency."""
    cid = _client_id(client)
    if cid not in _client_semaphores:
        # DC cluster (10.80.150.108) gets lower concurrency
        # because it has 3.6x more data and hits circuit breaker
        if "10.80.150.108" in cid:
            _client_semaphores[cid] = asyncio.Semaphore(_DEFAULT_DC_CONCURRENCY)
        else:
            _client_semaphores[cid] = asyncio.Semaphore(_DEFAULT_DRC_CONCURRENCY)
    return _client_semaphores[cid]


def _cache_key(index: str, body: dict) -> str:
    raw = f"{index}::{json.dumps(body, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(cache: dict, key: str) -> Optional[Any]:
    if key not in cache:
        return None
    ts, val = cache[key]
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return val


def _cache_set(cache: dict, key: str, val: Any) -> None:
    if len(cache) >= _CACHE_MAX_ENTRIES:
        oldest_key = min(cache, key=lambda k: cache[k][0])
        cache.pop(oldest_key, None)
    cache[key] = (time.monotonic(), val)


def clear_cache() -> None:
    _client_caches.clear()
    _client_semaphores.clear()


def drop_partial_tail(buckets: list[dict], bucket_seconds: int, lte_ms: int) -> list[dict]:
    """Drop trailing in-progress date_histogram buckets.

    A fixed_interval bucket starting at ``key`` covers ``[key, key + bucket_seconds)``.
    With ``lte_ms = now`` the final bucket captured only a slice of the interval, so a
    bytes/bucket_seconds rate divides a partial numerator by a full denominator → a fake
    ~0 at the chart edge. Buckets are ascending by key, so only trailing ones can be
    partial; historical ranges ending on a boundary keep every bucket.
    """
    interval_ms = bucket_seconds * 1000
    while buckets and buckets[-1]["key"] + interval_ms > lte_ms:
        buckets = buckets[:-1]
    return buckets


# ── Session-close-spike fix (shared by the traffic flow_chart timelines) ──
# FortiGate logs a flow's whole byte count against its @timestamp (log/close time), so a
# long session dumps minutes of bytes into one bucket → a fake Mbps spike. We re-spread
# the top-byte long sessions across the buckets they were actually active
# (flow.start.ms → flow.end.ms). Bounded + cheap: only sessions large enough to distort a
# bucket are candidates, sorted by bytes, capped.
_SPREAD_CAP = 6000
_SPREAD_MIN_BYTES = 10_000_000  # 10 MB / 60s ≈ 1.3 Mbps — below this a session can't spike
# Above this bucket size the spread is skipped: wide buckets (long time ranges) already
# average session-close spikes away, so almost no session exceeds one bucket — the fix
# would do nothing while still paying for a big sorted fetch. Keeps large-timeframe charts
# fast/stable. 900s ≈ up to a ~7.5h range at the frontend's ~30-bucket target.
_SPREAD_MAX_BUCKET_SECONDS = 900


async def spread_long_sessions(
    client: Any,
    base_filter: list[dict],
    key_field: str,
    key_name: Callable[[Any], str],
    charted_names: set[str],
    bucket_svc: dict[int, dict[str, float]],
    bucket_seconds: int,
    gte_ms: int,
    lte_ms: int,
    key_filter_values: list | None = None,
    name_of: Callable[[dict], str] | None = None,
    source_fields: list[str] | None = None,
) -> None:
    """Re-distribute long sessions' bytes across their active window, in place.

    `bucket_svc[bucket_start_ms][series_name] = bytes` holds the base per-bucket totals
    (built from the @timestamp date_histogram). `key_field` is the doc field the chart
    buckets on (e.g. flow.server.l4.port.id or flow.application.name); `key_name` maps a
    raw key to the chart's series name; only sessions whose name is in `charted_names`
    are touched. `key_filter_values` optionally restricts the fetch to those raw keys
    (cheaper when the caller knows the global top set). Bytes are conserved within the
    visible window; the pre-window slice of a session that started earlier is dropped.

    For the hybrid service dimension a single doc field isn't enough — the series name is
    resolve_service(application.name, port). Pass `name_of` (a callable over the hit
    `_source`) plus `source_fields` (the fields it reads); when given, they override the
    `key_field`/`key_name` single-field path so the re-spread lines up with the charted
    series. `key_filter_values` is ignored in that mode (charted_names still gates).
    """
    from app.opensearch._common import FLOW_INDEX

    if bucket_seconds > _SPREAD_MAX_BUCKET_SECONDS:
        return  # wide buckets already smooth spikes — skip the costly fetch (stability)

    W = bucket_seconds * 1000
    lo = (gte_ms // W) * W

    def _valid(t: int) -> bool:
        return lo <= t and (t + W) <= lte_ms  # matches drop_partial_tail (no partial tail)

    fetch_filter = base_filter + [{"range": {"flow.bytes": {"gte": _SPREAD_MIN_BYTES}}}]
    if key_filter_values and name_of is None:
        fetch_filter.append({"terms": {key_field: key_filter_values}})

    src = source_fields if name_of is not None else [key_field]
    resp = await safe_search(client, FLOW_INDEX, {
        "size": _SPREAD_CAP,
        "timeout": "115s",
        "query": {"bool": {"filter": fetch_filter}},
        "sort": [{"flow.bytes": "desc"}],
        "_source": ["flow.client.bytes", "flow.server.bytes", "flow.start.ms", "flow.end.ms", *src],
        "docvalue_fields": [{"field": "@timestamp", "format": "epoch_millis"}],
    })
    hits = resp.get("hits", {}).get("hits", [])
    for h in hits:
        s = h["_source"]
        st, en = s.get("flow.start.ms"), s.get("flow.end.ms")
        if st is None or en is None:
            continue
        dur = en - st
        if dur <= W:
            continue  # short session — already lands in ~one bucket
        b = float(int(s.get("flow.client.bytes", 0) or 0) + int(s.get("flow.server.bytes", 0) or 0))
        if b <= 0:
            continue
        name = name_of(s) if name_of is not None else key_name(s.get(key_field))
        if name not in charted_names:
            continue  # not a charted series — its bytes aren't shown anyway
        try:
            ts = int(h["fields"]["@timestamp"][0])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        # 1) remove the mis-attributed lump from the log-time bucket
        tb = (ts // W) * W
        if tb in bucket_svc and name in bucket_svc[tb]:
            bucket_svc[tb][name] = max(0.0, bucket_svc[tb][name] - b)
        # 2) add overlap-weighted slices across the session's active, in-window buckets
        t = (st // W) * W
        while t < en:
            if _valid(t):
                lov = max(t, st)
                hiv = min(t + W, en)
                if hiv > lov:
                    slot = bucket_svc.setdefault(t, {})
                    slot[name] = slot.get(name, 0.0) + b * (hiv - lov) / dur
            t += W
    if len(hits) >= _SPREAD_CAP:
        logger.info("spread_long_sessions: cap %d hit — smaller long sessions left in base", _SPREAD_CAP)


async def safe_search(
    client: AsyncOpenSearch,
    index: str,
    body: dict,
    use_cache: bool = True,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Execute an OpenSearch search query with:
    - Per-cluster concurrency limit (2 for DC, 4 for DRC) to prevent circuit breaker
    - Per-query timeout (asyncio.wait_for)
    - Optional in-memory caching (default 30s TTL)
    - Skeleton response on timeout/error

    Args:
        client: AsyncOpenSearch client
        index: Index pattern (e.g. "fortigate-appid-flow-*")
        body: Query body dict
        use_cache: If True, cache results for 30s (default)
        timeout_s: Query timeout in seconds (default from config)

    Returns:
        Search response dict with aggregations/hits keys always present
    """
    if timeout_s is None:
        timeout_s = settings.OPENSEARCH_QUERY_TIMEOUT

    cache_key: Optional[str] = None
    cache: dict = {}
    if use_cache:
        cache = _get_cache(client)
        cache_key = _cache_key(index, body)
        cached: dict[str, Any] | None = _cache_get(cache, cache_key)
        if cached is not None:
            return cached

    # Acquire per-cluster semaphore to prevent overwhelming the endpoint
    sem = _get_semaphore(client)
    async with sem:
        max_retries = 2  # retry up to 2x for transient circuit breaker errors
        for attempt in range(max_retries + 1):
            try:
                t0 = time.monotonic()
                resp = await asyncio.wait_for(
                    client.search(index=index, body=body),
                    timeout=timeout_s + 5,
                )
                elapsed = time.monotonic() - t0
                if elapsed > timeout_s * 0.5:
                    import logging
                    logger = logging.getLogger("nod.opensearch")
                    logger.warning(
                        f"Slow OpenSearch query: {elapsed:.1f}s / {timeout_s}s timeout — "
                        f"client={_client_id(client)} index={index}"
                    )
                resp_dict: dict[str, Any] = dict(resp) if not isinstance(resp, dict) else resp
                if "aggregations" not in resp_dict:
                    resp_dict["aggregations"] = {}
                if "hits" not in resp_dict:
                    resp_dict["hits"] = {"total": {"value": 0, "relation": "eq"}, "hits": []}
                # OpenSearch returns HTTP 200 with PARTIAL results when individual
                # shards fail (e.g. a terms agg against an index where the field was
                # dynamically mapped as `text`). Without this check the caller cannot
                # distinguish a complete answer from a silently undercounted one.
                shards = resp_dict.get("_shards") or {}
                failed_shards = shards.get("failed", 0)
                if failed_shards:
                    import logging
                    logger = logging.getLogger("nod.opensearch")
                    failures = shards.get("failures") or []
                    detail = "; ".join(
                        f"{f.get('index')}: {(f.get('reason') or {}).get('reason', '')[:120]}"
                        for f in failures[:3]
                    )
                    logger.error(
                        f"OpenSearch PARTIAL RESULTS — {failed_shards}/{shards.get('total')} shards failed, "
                        f"data is undercounted. client={_client_id(client)} index={index} :: {detail}"
                    )
                    resp_dict["_shard_failures"] = failed_shards
                    _record_degraded(
                        f"partial_results: {failed_shards}/{shards.get('total')} shards failed"
                    )
                if cache_key is not None:
                    _cache_set(cache, cache_key, resp_dict)
                return resp_dict
            except asyncio.TimeoutError:
                import logging
                logger = logging.getLogger("nod.opensearch")
                logger.error(f"OpenSearch query timeout after {timeout_s}s — client={_client_id(client)} index={index}")
                _record_degraded(f"timeout after {timeout_s}s")
                return {"aggregations": {}, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "_timed_out": True}
            except Exception as e:
                err_str = str(e)
                is_circuit_breaker = "circuit_breaking_exception" in err_str or "Data too large" in err_str
                if is_circuit_breaker and attempt < max_retries:
                    import logging
                    logger = logging.getLogger("nod.opensearch")
                    logger.warning(
                        f"DC circuit breaker hit, retrying in {2**attempt}s (attempt {attempt+1}/{max_retries+1}) — "
                        f"client={_client_id(client)} index={index}"
                    )
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff
                    continue
                import logging
                logger = logging.getLogger("nod.opensearch")
                logger.error(f"OpenSearch query error: {type(e).__name__}: {e}")
                _record_degraded(
                    "circuit_breaker: cluster out of memory" if is_circuit_breaker
                    else f"query_error: {type(e).__name__}"
                )
                return {"aggregations": {}, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "_error": err_str[:200]}

    # Defensive fallback — every code path above returns, but mypy doesn't
    # always narrow through for/continue, so this satisfies the type checker.
    return {"aggregations": {}, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "_error": "unreachable"}
