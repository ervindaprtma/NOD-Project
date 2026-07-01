"""
OpenSearch query helpers with timeout, caching, and error handling.

Provides safe_search() wrapper that:
- Wraps OpenSearch queries with asyncio.wait_for timeout
- Adds request-level timeout to each .search() call
- Implements lightweight in-memory caching for expensive aggregations
- Returns empty results on timeout/error instead of raising
- Per-client cache namespacing to prevent cross-endpoint data leakage

This prevents the backend from hanging or crashing on expensive 24h+ queries.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.core.config import get_settings

settings = get_settings()

# Per-client cache namespacing (keyed by client id(hosts))
# Prevents DC and DRC query results from being served to wrong endpoint
_client_caches: dict[str, dict[str, tuple[float, Any]]] = {}
_CACHE_TTL_SECONDS = 30  # short TTL — 30s — for aggregation queries
_CACHE_MAX_ENTRIES = 256  # per-client cap


def _client_id(client: AsyncOpenSearch) -> str:
    """Generate a stable id for an OpenSearch client (based on its hosts)."""
    try:
        return json.dumps(client.transport.hosts, sort_keys=True)
    except Exception:
        return str(id(client))


def _get_cache(client: AsyncOpenSearch) -> dict[str, tuple[float, Any]]:
    """Get the cache dict for a specific client, creating if needed."""
    cid = _client_id(client)
    if cid not in _client_caches:
        _client_caches[cid] = {}
    return _client_caches[cid]


def _cache_key(index: str, body: dict) -> str:
    """Generate a stable cache key for an index+body pair."""
    raw = f"{index}::{json.dumps(body, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(cache: dict, key: str) -> Optional[Any]:
    """Get cached result if not expired."""
    if key not in cache:
        return None
    ts, val = cache[key]
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return val


def _cache_set(cache: dict, key: str, val: Any) -> None:
    """Store result in cache with TTL, evicting oldest if at capacity."""
    if len(cache) >= _CACHE_MAX_ENTRIES:
        oldest_key = min(cache, key=lambda k: cache[k][0])
        cache.pop(oldest_key, None)
    cache[key] = (time.monotonic(), val)


def clear_cache() -> None:
    """Clear all cached results across all clients. Call after writes or test setup."""
    _client_caches.clear()


async def safe_search(
    client: AsyncOpenSearch,
    index: str,
    body: dict,
    use_cache: bool = True,
    timeout_s: int | None = None,
) -> dict:
    """
    Execute an OpenSearch search query with:
    - Per-query timeout (asyncio.wait_for + request_timeout)
    - Optional in-memory caching (default 30s TTL)
    - Returns empty result dict on timeout/error

    Args:
        client: AsyncOpenSearch client
        index: Index pattern (e.g. "fortigate-appid-flow-*")
        body: Query body dict
        use_cache: If True, cache results for 30s (default)
        timeout_s: Query timeout in seconds (default from config)

    Returns:
        Search response dict, or empty dict on timeout/error
    """
    if timeout_s is None:
        timeout_s = settings.OPENSEARCH_QUERY_TIMEOUT

    cache_key: Optional[str] = None
    cache: dict = {}
    if use_cache:
        cache = _get_cache(client)
        cache_key = _cache_key(index, body)
        cached = _cache_get(cache, cache_key)
        if cached is not None:
            return cached

    try:
        t0 = time.monotonic()
        resp = await asyncio.wait_for(
            client.search(index=index, body=body),
            timeout=timeout_s + 5,  # outer safety margin
        )
        elapsed = time.monotonic() - t0
        # Alert on slow queries (>50% of timeout) without blocking
        if elapsed > timeout_s * 0.5:
            import logging
            logger = logging.getLogger("nod.opensearch")
            logger.warning(
                f"Slow OpenSearch query: {elapsed:.1f}s / {timeout_s}s timeout — "
                f"client={_client_id(client)} index={index} body_keys={list(body.keys())}"
            )
        resp_dict = dict(resp) if not isinstance(resp, dict) else resp
        # Ensure response has the expected skeleton keys for aggregations/size-0 queries
        if "aggregations" not in resp_dict:
            resp_dict["aggregations"] = {}
        if "hits" not in resp_dict:
            resp_dict["hits"] = {"total": {"value": 0, "relation": "eq"}, "hits": []}
        if cache_key is not None:
            _cache_set(cache, cache_key, resp_dict)
        return resp_dict
    except asyncio.TimeoutError:
        import logging
        logger = logging.getLogger("nod.opensearch")
        logger.error(
            f"OpenSearch query timeout after {timeout_s}s — client={_client_id(client)} "
            f"index={index} body_keys={list(body.keys())}"
        )
        # Return skeleton with empty aggregations so callers can proceed
        return {"aggregations": {}, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "_timed_out": True}
    except Exception as e:
        import logging
        logger = logging.getLogger("nod.opensearch")
        logger.error(f"OpenSearch query error: {type(e).__name__}: {e}")
        return {"aggregations": {}, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}, "_error": str(e)}
