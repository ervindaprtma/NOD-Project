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
from contextvars import ContextVar
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.core.config import get_settings

settings = get_settings()

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
