"""
Async OpenSearch client instances — one per configured endpoint.
Provides singleton-style client factories (not global instances) for testability.

Cluster naming:
  - opensearch-dc  (10.80.150.108:9200) — DC site cluster
  - opensearch-drc (10.90.150.108:9200) — DRC site cluster (shared with Office)
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from opensearchpy import AsyncOpenSearch
from opensearchpy.exceptions import OpenSearchException
import logging

from app.core.config import get_settings

settings = get_settings()

# Methods we consider "query" for System-Logs purposes: errors on these
# usually mean the dashboard request itself failed. Admin/health methods
# (`ping`, `cluster.health`, `indices.*`) are already covered by health events.
_QUERY_METHODS = ("search", "count", "msearch", "explain", "get", "bulk")


def _safe_arg(args: tuple, kwargs: dict, *names: str) -> str | None:
    """Pick the first present value across positional/kwargs without copying the DSL body."""
    for n in names:
        if n in kwargs and isinstance(kwargs[n], (str, int)):
            return str(kwargs[n])
    if args:
        v = args[0]
        if isinstance(v, (str, int)):
            return str(v)
    return None


def _size_from(body: Any) -> int | None:
    """Extract the requested `size` from a query body, if any, without copying the rest."""
    if not isinstance(body, dict):
        return None
    v = body.get("size")
    return int(v) if isinstance(v, int) else None


def _wrap_query_errors(client: AsyncOpenSearch, cluster: str) -> AsyncOpenSearch:
    """Wrap every query method: mirror success + failure to the System Logs sink.

    Success → INFO `query.opensearch_request` (status, took_ms, index, size — NO body).
    Failure → ERROR `query.opensearch_error`, then re-raise unchanged.

    The endpoint URL (and its basic-auth credential) is never put in the row; any
    `password=…` / JWT / basic-auth URL that surfaces in an exception message is
    scrubbed by `_redact` inside `log_event`.
    """
    from app.services.system_logger import log_event  # local import: avoid cycles at import time

    for method_name in _QUERY_METHODS:
        original = getattr(client, method_name)

        async def wrapped(*args, __name=method_name, __orig=original, **kwargs):
            index = _safe_arg(args, kwargs, "index")
            body = kwargs.get("body") or (args[1] if len(args) > 1 else None)
            size = _size_from(body)
            started = time.monotonic()
            try:
                resp = await __orig(*args, **kwargs)
            except OpenSearchException as exc:
                took_ms = int((time.monotonic() - started) * 1000)
                log_event(
                    level="ERROR",
                    category="query",
                    event="query.opensearch_error",
                    message=f"OpenSearch {__name} failed on {cluster}: {type(exc).__name__}: {exc}",
                    method=__name,
                    details={
                        "cluster": cluster,
                        "index": index,
                        "error_type": type(exc).__name__,
                        "took_ms": took_ms,
                    },
                )
                raise

            took_ms = int((time.monotonic() - started) * 1000)
            status = getattr(resp, "status_code", None) or 200
            details: dict[str, Any] = {
                "cluster": cluster,
                "index": index,
                "took_ms": took_ms,
                "status": status,
            }
            if size is not None:
                details["size"] = size
            log_event(
                level="INFO",
                category="query",
                event="query.opensearch_request",
                message=f"OpenSearch {__name} on {cluster}: {status} in {took_ms}ms"
                        + (f" [{index}]" if index else ""),
                method=__name,
                details=details,
            )
            return resp

        wrapped.__name__ = method_name
        setattr(client, method_name, wrapped)

    return client


def _build_client(hosts: str, cluster: str = "unknown") -> AsyncOpenSearch:
    """Create an AsyncOpenSearch client for a given endpoint."""
    use_ssl = hosts.startswith("https://")
    kwargs: dict = {
        "hosts": [hosts],
        "timeout": settings.OPENSEARCH_REQUEST_TIMEOUT,
        "maxsize": settings.OPENSEARCH_POOL_SIZE,
        "retry_on_timeout": True,
        "max_retries": 2,
        "use_ssl": use_ssl,
        "verify_certs": settings.OPENSEARCH_VERIFY_CERTS if use_ssl else False,
        "ssl_show_warn": not settings.OPENSEARCH_VERIFY_CERTS,
    }
    if use_ssl and settings.OPENSEARCH_VERIFY_CERTS and settings.OPENSEARCH_CA_CERT_PATH:
        kwargs["ca_certs"] = settings.OPENSEARCH_CA_CERT_PATH
    if settings.OPENSEARCH_USERNAME and settings.OPENSEARCH_PASSWORD:
        kwargs["http_auth"] = (settings.OPENSEARCH_USERNAME, settings.OPENSEARCH_PASSWORD)
    if use_ssl and not settings.OPENSEARCH_VERIFY_CERTS:
        logger = logging.getLogger("nod.opensearch")
        logger.warning("OpenSearch TLS cert verification disabled — set OPENSEARCH_VERIFY_CERTS=true for production")
    return _wrap_query_errors(AsyncOpenSearch(**kwargs), cluster)


@lru_cache()
def get_dc_client() -> AsyncOpenSearch:
    """Client for DC OpenSearch cluster (10.80.150.108:9200)."""
    return _build_client(settings.OPENSEARCH_DC_URL, cluster="opensearch-dc")


@lru_cache()
def get_drc_client() -> AsyncOpenSearch:
    """Client for DRC OpenSearch cluster (10.90.150.108:9200)."""
    return _build_client(settings.OPENSEARCH_DRC_URL, cluster="opensearch-drc")


@lru_cache()
def get_ipsec_client() -> AsyncOpenSearch:
    """Client for ipsec-* index."""
    return _build_client(settings.OPENSEARCH_IPSEC_URL, cluster="opensearch-ipsec")


async def check_opensearch_health(client: AsyncOpenSearch) -> bool:
    """Ping an OpenSearch cluster. Returns True if healthy."""
    try:
        result: bool = await client.ping()
        return result
    except Exception:
        return False


async def check_all_clusters() -> dict[str, bool]:
    """Check health of all configured OpenSearch clusters."""
    results = {}
    try:
        results["opensearch_dc"] = await check_opensearch_health(get_dc_client())
    except Exception:
        results["opensearch_dc"] = False
    try:
        results["opensearch_drc"] = await check_opensearch_health(get_drc_client())
    except Exception:
        results["opensearch_drc"] = False
    try:
        results["opensearch_ipsec"] = await check_opensearch_health(get_ipsec_client())
    except Exception:
        results["opensearch_ipsec"] = False
    return results
