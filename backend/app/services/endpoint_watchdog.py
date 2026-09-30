"""Endpoint watchdog — alerts when an OpenSearch cluster stops answering.

Dashboards already degrade gracefully when a query times out (safe_search returns a
skeleton + the amber DegradedBanner), but that only shows a user who happens to be on
the page. This watchdog actively pings each configured cluster on a fixed interval and
pushes a notification (same channels as the alert engine) + a System Logs row when an
endpoint has been unreachable/timing-out for a sustained window, and again when it
recovers.

State is in-memory (process-local, matches the in-process scheduler); a restart clears
it, so a still-down endpoint re-alerts once after the next threshold — acceptable.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone

from app.core.config import get_settings
from app.opensearch.client import (
    check_opensearch_health,
    get_dc_client,
    get_drc_client,
    get_ipsec_client,
)

logger = logging.getLogger("nod.watchdog")

# Human label -> client factory. Labels are what the operator sees in the alert.
_CLUSTERS = {
    "OpenSearch DC": get_dc_client,
    "OpenSearch DRC": get_drc_client,
    "OpenSearch IPsec": get_ipsec_client,
}

# label -> {"fails": consecutive failed probes, "alerted": down-alert already sent}
_state: dict[str, dict] = {}

_SEVERITY = "CRITICAL"  # down + recovery share it so both hit the same channel set


async def _ping(get_client, timeout_s: float) -> bool:
    """True if the cluster answers a ping within timeout_s. A hang counts as down —
    this is exactly the 'endpoint timeout' the watchdog exists to catch."""
    try:
        return await asyncio.wait_for(check_opensearch_health(get_client()), timeout=timeout_s)
    except Exception:
        return False


async def _dispatch(subject: str, body: str, body_html: str) -> None:
    """Send to every enabled channel that accepts CRITICAL. Best-effort per channel.

    Telegram gets the HTML body (bold) with parse_mode="HTML"; every other channel gets
    the plain body — `<b>` tags would render literally on Discord/email/WhatsApp."""
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
    """One probe cycle: ping all clusters concurrently, drive each state machine."""
    from app.services.system_logger import log_event

    s = get_settings()
    interval = s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS
    threshold = s.OPENSEARCH_HEALTH_FAIL_THRESHOLD
    timeout_s = s.OPENSEARCH_HEALTH_PING_TIMEOUT_SECONDS

    labels = list(_CLUSTERS)
    results = await asyncio.gather(*(_ping(_CLUSTERS[l], timeout_s) for l in labels))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for label, ok in zip(labels, results):
        st = _state.setdefault(label, {"fails": 0, "alerted": False})
        if ok:
            if st["alerted"]:
                msg = f"✅ {label} recovered — answering health pings again at {now}."
                msg_html = (f"✅ <b>{html.escape(label)} recovered</b> — answering health "
                            f"pings again at {html.escape(now)}.")
                await _dispatch(f"{label} recovered", msg, msg_html)
                log_event(level="INFO", category="system", event="endpoint_recovered",
                          message=msg, details={"cluster": label})
                logger.warning("%s recovered after %d failed probes", label, st["fails"])
            st["fails"] = 0
            st["alerted"] = False
            continue

        st["fails"] += 1
        if st["fails"] >= threshold and not st["alerted"]:
            down_s = st["fails"] * interval
            msg = (f"🚨 {label} is not responding — health ping has failed/timed out "
                   f"{st['fails']} times (~{down_s}s). Dashboards backed by this endpoint "
                   f"will show 'Data unavailable'.")
            msg_html = (f"🚨 <b>{html.escape(label)} DOWN</b> — health ping has failed/timed "
                        f"out <b>{st['fails']}</b> times (~{down_s}s). Dashboards backed by "
                        f"this endpoint will show 'Data unavailable'.")
            await _dispatch(f"{label} DOWN", msg, msg_html)
            log_event(level="ERROR", category="system", event="endpoint_down", message=msg,
                      details={"cluster": label, "consecutive_fails": st["fails"], "down_seconds": down_s})
            st["alerted"] = True
            logger.error("%s DOWN — %d consecutive failed probes", label, st["fails"])
        else:
            # Interim failure below threshold: log line only, no System Logs spam every 30s.
            logger.warning("%s health ping failed (%d/%d)", label, st["fails"], threshold)


def start_endpoint_watchdog() -> None:
    """Register the probe on the already-running alert scheduler (no extra infra)."""
    from app.services.alert_engine import scheduler

    s = get_settings()
    scheduler.add_job(
        probe_endpoints, "interval",
        seconds=s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS,
        id="endpoint_watchdog", replace_existing=True,
        misfire_grace_time=s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS, max_instances=1,
    )
    logger.info(
        "Endpoint watchdog started (interval=%ss, ping_timeout=%ss, fail_threshold=%s → alert after ~%ss down)",
        s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS, s.OPENSEARCH_HEALTH_PING_TIMEOUT_SECONDS,
        s.OPENSEARCH_HEALTH_FAIL_THRESHOLD,
        s.OPENSEARCH_HEALTH_FAIL_THRESHOLD * s.OPENSEARCH_HEALTH_PROBE_INTERVAL_SECONDS,
    )
