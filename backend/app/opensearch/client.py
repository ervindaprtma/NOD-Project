"""
Async OpenSearch client instances — one per configured endpoint.
Provides singleton-style client factories (not global instances) for testability.

Cluster naming:
  - opensearch-dc  (10.80.150.108:9200) — DC site cluster
  - opensearch-drc (10.90.150.108:9200) — DRC site cluster (shared with Office)
"""
from __future__ import annotations

from functools import lru_cache

from opensearchpy import AsyncOpenSearch
import logging

from app.core.config import get_settings

settings = get_settings()


def _build_client(hosts: str) -> AsyncOpenSearch:
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
    return AsyncOpenSearch(**kwargs)


@lru_cache()
def get_dc_client() -> AsyncOpenSearch:
    """Client for DC OpenSearch cluster (10.80.150.108:9200)."""
    return _build_client(settings.OPENSEARCH_DC_URL)


@lru_cache()
def get_drc_client() -> AsyncOpenSearch:
    """Client for DRC OpenSearch cluster (10.90.150.108:9200)."""
    return _build_client(settings.OPENSEARCH_DRC_URL)


@lru_cache()
def get_ipsec_client() -> AsyncOpenSearch:
    """Client for ipsec-* index."""
    return _build_client(settings.OPENSEARCH_IPSEC_URL)


async def check_opensearch_health(client: AsyncOpenSearch) -> bool:
    """Ping an OpenSearch cluster. Returns True if healthy."""
    try:
        return await client.ping()
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
