"""Device IP-alias lookup for re-IPed devices (Availability stitching).

An explicit superadmin-managed mapping (old tag.source IP → current IP) lets
device_uptime stitch both eras under one device card when a device is re-IPed.
Never auto-inferred from hostname — two devices sharing a hostname must not
silently merge.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from sqlalchemy import select

from app.db.models import DeviceIpAlias
from app.db.session import AsyncSessionLocal

_MAP_TTL_SECONDS = 15 * 60
_map_cache: Dict[str, tuple[float, Dict[str, str]]] = {}   # endpoint-ish key → (ts, {old: current})


def _cache_get(key: str) -> Optional[Dict[str, str]]:
    hit = _map_cache.get(key)
    if not hit:
        return None
    ts, mapping = hit
    if time.monotonic() - ts > _MAP_TTL_SECONDS:
        _map_cache.pop(key, None)
        return None
    return mapping


def _cache_put(key: str, mapping: Dict[str, str]) -> None:
    _map_cache[key] = (time.monotonic(), mapping)


async def fetch_alias_map() -> Dict[str, str]:
    """{old_ip: current_ip} from Postgres, 15-min cached. Empty dict on any
    failure — degraded mode beats a broken page (mirrors fetch_site_map)."""
    cached = _cache_get("aliases")
    if cached is not None:
        return cached
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(select(DeviceIpAlias))).scalars().all()
        mapping = {r.old_ip: r.current_ip for r in rows}
        _cache_put("aliases", mapping)
        return mapping
    except Exception:
        return {}


def reverse_aliases(alias_map: Dict[str, str]) -> Dict[str, list[str]]:
    """{current_ip: [old_ips]} — for merging era buckets under one card."""
    out: Dict[str, list[str]] = {}
    for old, current in alias_map.items():
        out.setdefault(current, []).append(old)
    return out


def resolve_current_ip(ip: str, alias_map: Dict[str, str]) -> str:
    """Canonical (current) IP for a bucket key — old IPs resolve forward."""
    return alias_map.get(ip, ip)
