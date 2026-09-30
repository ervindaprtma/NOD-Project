"""Data-source watchdog — alerts when an OpenSearch data source stops returning data.

Instead of a plain cluster ping, each probe runs a "last-data" trial query per logical
data source (a size:1, newest-first fetch with that source's real index+filter) and reads
the age of the newest document. That answers the operator's real question — "is each aspect
of the data current?" — not just "does the cluster answer TCP?".

Five sources (see _SOURCES): NetFlow DC/DRC/Office and SNMP DC/DRC.

Status per source:
  healthy — reachable and newest doc within the freshness budget
  stale   — reachable but newest doc older than the budget (SNMP: collector likely stalled;
            NetFlow: could just be a quiet window)
  no_data — reachable, index exists, but the filter matches no documents at all
  down    — the trial query errored or timed out (cluster unreachable / index missing)

Paging: only `down` pages ops by default (never false-fires on quiet traffic). SNMP `stale`
also pages when OPENSEARCH_HEALTH_ALERT_ON_STALE is on. Everything shows on the System
Health tab with the newest-doc age.

State is in-memory (process-local, matches the in-process scheduler).
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.opensearch._common import FLOW_INDEX
from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search
from app.opensearch.traffic_flow import SITE_SOURCE_IPS

logger = logging.getLogger("nod.watchdog")

# ── The five data sources (label → how to trial-get its latest doc) ──────────────
# kind drives the freshness budget: "snmp" tight (telegraf ~30s poll), "netflow" wide.
_SNMP_INDEX = "telegraf-index*,ipsec-*"


def _flow_filter(site: str) -> list[dict]:
    return [{"term": {"flow.export.ip.addr": SITE_SOURCE_IPS[site]}}]


_SOURCES: list[dict] = [
    {"name": "NetFlow DC", "client": get_dc_client, "index": FLOW_INDEX,
     "filter": _flow_filter("Site_FGT-DC"), "kind": "netflow"},
    {"name": "NetFlow DRC", "client": get_drc_client, "index": FLOW_INDEX,
     "filter": _flow_filter("Site_FGT-DRC"), "kind": "netflow"},
    {"name": "NetFlow Office", "client": get_drc_client, "index": FLOW_INDEX,
     "filter": _flow_filter("Site_FGT_Office"), "kind": "netflow"},
    {"name": "SNMP DC", "client": get_dc_client, "index": _SNMP_INDEX,
     "filter": [], "kind": "snmp"},
    {"name": "SNMP DRC", "client": get_drc_client, "index": _SNMP_INDEX,
     "filter": [], "kind": "snmp"},
]

# label -> {fails, alerted, status, last_change, last_doc_ms, last_age_s, reachable, kind}
_state: dict[str, dict] = {}

# Process start — the UI flags "since backend start" when a status hasn't flipped since.
_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

_SEVERITY = "CRITICAL"  # down + recovery share it so both hit the same channel set


def get_state() -> dict:
    """Read-only snapshot for the System Health page. Copies so callers can't mutate."""
    return {
        "started_at": _STARTED_AT,
        "sources": [
            {
                "name": s["name"],
                "kind": st.get("kind"),
                "status": st.get("status", "healthy"),
                "last_doc_ms": st.get("last_doc_ms"),
                "last_age_seconds": st.get("last_age_s"),
                "reachable": st.get("reachable", True),
                "consecutive_fails": st.get("fails", 0),
                "alerted": st.get("alerted", False),
                "last_change": st.get("last_change"),
            }
            for s in _SOURCES
            for st in [_state.get(s["name"], {})]
        ],
    }


async def _last_data(src: dict, timeout_s: float) -> tuple[bool, int | None]:
    """Trial-get: (reachable, newest_doc_epoch_ms|None). safe_search never raises — it
    returns a skeleton with _timed_out/_error on failure, which we read as unreachable."""
    body = {
        "size": 1,
        "query": {"bool": {"filter": src["filter"]}},
        "sort": [{"@timestamp": {"order": "desc"}}],
        "_source": False,
        "docvalue_fields": [{"field": "@timestamp", "format": "epoch_millis"}],
    }
    try:
        resp = await safe_search(src["client"](), src["index"], body,
                                 use_cache=False, timeout_s=int(timeout_s))
    except Exception:
        return False, None
    if resp.get("_timed_out") or resp.get("_error"):
        return False, None
    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return True, None  # reachable, but filter matched nothing
    try:
        return True, int(hits[0]["fields"]["@timestamp"][0])
    except (KeyError, IndexError, ValueError, TypeError):
        return True, None


def _derive_status(reachable: bool, last_ms: int | None, now_ms: int, kind: str, s) -> str:
    if not reachable:
        return "down"
    if last_ms is None:
        return "no_data"
    budget = (s.OPENSEARCH_HEALTH_FRESH_SNMP_SECONDS if kind == "snmp"
              else s.OPENSEARCH_HEALTH_FRESH_NETFLOW_SECONDS)
    return "healthy" if (now_ms - last_ms) / 1000 <= budget else "stale"


async def _dispatch(subject: str, body: str, body_html: str) -> None:
    """Send to every enabled channel that accepts CRITICAL. Best-effort per channel.
    Telegram gets the HTML (bold) body; other channels get plain text."""
    from app.services.notifier_helper import load_channel_configs, send_alert

    configs = await load_channel_configs(min_severity=_SEVERITY)
    if not configs:
        logger.warning("watchdog: no enabled channels accept %s — alert not delivered", _SEVERITY)
        return
    for channel, cfg in configs.items():
        try:
            if channel == "telegram":
                await send_alert(channel=channel, config=cfg, subject=subject,
                                 body=body_html, severity=_SEVERITY, parse_mode="HTML")
            else:
                await send_alert(channel=channel, config=cfg, subject=subject,
                                 body=body, severity=_SEVERITY)
        except Exception as e:  # a bad channel must not stop the others
            logger.error("watchdog notify via %s failed: %s", channel, e)


async def probe_endpoints() -> None:
    """One probe cycle: trial-get every source concurrently, drive each state machine."""
    from app.services.system_logger import log_event

    s = get_settings()
    interval = s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS
    threshold = s.OPENSEARCH_HEALTH_FAIL_THRESHOLD
    timeout_s = s.OPENSEARCH_HEALTH_PING_TIMEOUT_SECONDS
    alert_on_stale = s.OPENSEARCH_HEALTH_ALERT_ON_STALE

    now_ms = int(time.time() * 1000)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = await asyncio.gather(*(_last_data(src, timeout_s) for src in _SOURCES))

    for src, (reachable, last_ms) in zip(_SOURCES, results):
        name, kind = src["name"], src["kind"]
        st = _state.setdefault(name, {"fails": 0, "alerted": False, "status": "healthy",
                                      "last_change": _STARTED_AT, "kind": kind})
        status = _derive_status(reachable, last_ms, now_ms, kind, s)
        # Clamp ≥0: DRC cluster doc timestamps can run slightly ahead of the backend clock
        # (skew), which would otherwise render as a nonsensical negative "last data -76s".
        age_s = max(0, round((now_ms - last_ms) / 1000)) if last_ms is not None else None
        st.update(status=status, last_doc_ms=last_ms, last_age_s=age_s,
                  reachable=reachable, kind=kind)

        # "bad" = the alertable condition: unreachable always; SNMP-stale only if opted in.
        bad = status == "down" or (status == "stale" and kind == "snmp" and alert_on_stale)
        age_txt = f"{age_s}s ago" if age_s is not None else "no matching data"
        reason = ("query error/timeout — source unreachable" if status == "down"
                  else f"stale — newest data {age_txt}")

        if not bad:
            if st["alerted"]:
                msg = f"✅ {name} recovered — data is current again ({age_txt}) at {now}."
                msg_html = (f"✅ <b>{html.escape(name)} recovered</b> — data is current again "
                            f"({html.escape(age_txt)}) at {html.escape(now)}.")
                await _dispatch(f"{name} recovered", msg, msg_html)
                log_event(level="INFO", category="system", event="endpoint_recovered",
                          message=msg, details={"source": name, "status": status})
                logger.warning("%s recovered after %d bad probes", name, st["fails"])
            st["fails"] = 0
            st["alerted"] = False
        else:
            st["fails"] += 1
            if st["fails"] >= threshold and not st["alerted"]:
                down_s = st["fails"] * interval
                msg = (f"🚨 {name} unhealthy — {reason} for ~{down_s}s. "
                       f"Dashboards backed by this source may show 'Data unavailable'.")
                msg_html = (f"🚨 <b>{html.escape(name)} unhealthy</b> — {html.escape(reason)} "
                            f"for ~{down_s}s. Dashboards backed by this source may show "
                            f"'Data unavailable'.")
                await _dispatch(f"{name} unhealthy", msg, msg_html)
                log_event(level="ERROR", category="system", event="endpoint_down", message=msg,
                          details={"source": name, "status": status,
                                   "consecutive_fails": st["fails"], "down_seconds": down_s})
                st["alerted"] = True
                logger.error("%s unhealthy (%s) — %d consecutive bad probes", name, status, st["fails"])
            else:
                logger.warning("%s probe bad: %s (%d/%d)", name, reason, st["fails"], threshold)

        if status != st.get("_prev_status"):
            st["last_change"] = now
            st["_prev_status"] = status


def start_endpoint_watchdog() -> None:
    """Register the probe on the already-running alert scheduler (no extra infra)."""
    from app.services.alert_engine import scheduler

    # Pre-seed so the System Health page lists every source before the first probe lands.
    for src in _SOURCES:
        _state.setdefault(src["name"], {"fails": 0, "alerted": False, "status": "healthy",
                                        "last_change": _STARTED_AT, "kind": src["kind"]})

    s = get_settings()
    scheduler.add_job(
        probe_endpoints, "interval",
        seconds=s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS,
        id="endpoint_watchdog", replace_existing=True,
        misfire_grace_time=s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS, max_instances=1,
        next_run_time=datetime.now(timezone.utc),  # first probe now, not after one interval
    )
    logger.info(
        "Data-source watchdog started (%d sources, interval=%ss, timeout=%ss, threshold=%s, "
        "fresh snmp=%ss/netflow=%ss, alert_on_stale=%s)",
        len(_SOURCES), s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS,
        s.OPENSEARCH_HEALTH_PING_TIMEOUT_SECONDS, s.OPENSEARCH_HEALTH_FAIL_THRESHOLD,
        s.OPENSEARCH_HEALTH_FRESH_SNMP_SECONDS, s.OPENSEARCH_HEALTH_FRESH_NETFLOW_SECONDS,
        s.OPENSEARCH_HEALTH_ALERT_ON_STALE,
    )
