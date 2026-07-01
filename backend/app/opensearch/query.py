"""
OpenSearch query helpers with timeout, caching, and error handling.

Provides safe_search() wrapper that:
- Wraps OpenSearch queries with asyncio.wait_for timeout
- Adds request-level timeout to each .search() call
- Implements lightweight in-memory caching for expensive aggregations
- Returns empty results on timeout/error instead of raising

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

# In-memory cache for query results (TTL-based)
_query_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 30  # short TTL — 30s — for aggregation queries
_CACHE_MAX_ENTRIES = 256


def _cache_key(index: str, body: dict) -> str:
    """Generate a stable cache key for an index+body pair."""
    raw = f"{index}::{json.dumps(body, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[Any]:
    """Get cached result if not expired."""
    if key not in _query_cache:
        return None
    ts, val = _query_cache[key]
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _query_cache.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    """Store result in cache with TTL, evicting oldest if at capacity."""
    if len(_query_cache) >= _CACHE_MAX_ENTRIES:
        # Evict oldest entry
        oldest_key = min(_query_cache, key=lambda k: _query_cache[k][0])
        _query_cache.pop(oldest_key, None)
    _query_cache[key] = (time.monotonic(), val)


def clear_cache() -> None:
    """Clear all cached results. Call after writes or test setup."""
    _query_cache.clear()


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

    cache_key = _cache_key(index, body) if use_cache else None
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    try:
        resp = await asyncio.wait_for(
            client.search(index=index, body=body),
            timeout=timeout_s + 5,  # outer safety margin
        )
        resp_dict = dict(resp) if not isinstance(resp, dict) else resp
        if cache_key:
            _cache_set(cache_key, resp_dict)
        return resp_dict
    except asyncio.TimeoutError:
        return {}
    except Exception:
        return {}
