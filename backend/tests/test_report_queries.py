"""
Guards the two regressions that silently emptied R-03 and R-09.

Both were contract drifts between modules, not logic bugs — the kind a type
checker misses because the call sites use dynamic dicts.
"""
import inspect

import pytest

from app.api.interface_stats import _compute_throughput_timeline
from app.opensearch import device_uptime, ipsec, sslvpn, traffic_inbound, traffic_internal
from app.services.report_generator import (
    _auto_interval_str,
    _report_title,
    _sla_link_compliance,
    build_report_context,
)


def test_interface_timeline_returns_triple():
    """R-09 unpacks (points, in_bytes, out_bytes); a bare list breaks it."""
    src = inspect.getsource(_compute_throughput_timeline)
    assert "tuple[list[InterfaceTimelinePoint], float, float]" in src

    points, in_bytes, out_bytes = _compute_throughput_timeline(
        [
            {"key": 0, "max_in_octets": {"value": 0}, "max_out_octets": {"value": 0}},
            {"key": 60_000, "max_in_octets": {"value": 7_500_000}, "max_out_octets": {"value": 750_000}},
        ],
        interval_seconds=60,
    )
    assert [p.in_mbps for p in points] == [1.0]  # 7.5 MB × 8 / 60s / 1e6
    assert (in_bytes, out_bytes) == (7_500_000, 750_000)


def test_vpn_histograms_accept_report_intervals():
    """
    R-03 feeds _auto_interval_str() output straight into these aggs.
    calendar_interval only accepts single-unit intervals (1h, 1d, …), so
    '15m'/'5m'/'60s'/'6h' returned a 400 and nulled the timelines.
    """
    hour = 3_600_000
    produced = {
        _auto_interval_str(0, span)
        for span in (hour, 4 * hour, 24 * hour, 7 * 24 * hour, 30 * 24 * hour)
    }
    assert produced - {"1h", "1d", "1w", "1M", "1q", "1y"}, "test is moot if all are calendar-safe"

    for mod in (ipsec, sslvpn):
        src = inspect.getsource(mod)
        assert '"calendar_interval": interval' not in src, f"{mod.__name__} rejects {produced}"


def test_sessions_counted_by_correlation_id_not_connection_id():
    """A 'session' = one client-port↔server-port connection = one flow.correlation_id.
    connection_id is a coarse conversation key (one value spans tens of thousands of
    client ports) and under-counts sessions ~180x — it must never come back as the
    session_count field. Guards all three traffic surfaces (Internet/Inbound/Internal)."""
    from app.opensearch import traffic_flow, traffic_inbound, traffic_internal
    for mod in (traffic_flow, traffic_inbound, traffic_internal):
        src = inspect.getsource(mod)
        assert '"session_count": {"cardinality": {"field": "flow.correlation_id"}}' in src, \
            f"{mod.__name__} must count sessions by correlation_id"
        assert '"session_count": {"cardinality": {"field": "flow.connection_id"}}' not in src, \
            f"{mod.__name__} regressed to connection_id for session_count"


def test_unknown_site_matches_nothing():
    """An empty IP term fails the shard instead of returning no hits."""
    for mod in (traffic_inbound, traffic_internal):
        assert mod._site_filter("Site_Does_Not_Exist") == {"match_none": {}}
        assert "term" in mod._site_filter("Site_FGT-DC")


@pytest.mark.asyncio
async def test_r10_reads_window_from_table_interval(monkeypatch):
    """
    R-10 has no dedicated window column: it carries the SLA window in
    `table_interval`. Guards that build_report_context routes it into
    device_availability(window=...) and maps the result into the template shape.
    """
    assert _report_title("R-10") == "Device Availability Report"

    captured = {}

    async def fake_availability(site_name, window, **kwargs):
        captured["window"] = window
        return {
            "summary": {"devices_total": 1, "devices_reporting": 1, "reboots_total": 0,
                        "collector_gaps": [{"start_ms": 0, "end_ms": 60_000, "duration_seconds": 60}],
                        "history_sufficient": False},
            "devices": [{"hostname": "SW-1", "vendor": "cisco", "status": "up",
                         "availability_pct": 99.9, "uptime_human_long": "1 day",
                         "boot_time_ms": 1_700_000_000_000, "reboot_count": 0,
                         "total_downtime_seconds": 42, "wrap_risk": False, "partial_history": True}],
        }

    monkeypatch.setattr(device_uptime, "device_availability", fake_availability)

    ctx = await build_report_context(
        report_type="R-10", gte_ms=0, lte_ms=1,
        sites=["Site_FGT-DC"], table_interval="30d",
    )
    da = ctx["report_data"]["device_availability"]
    assert captured["window"] == "30d"       # table_interval → window, not the 0..1 range
    assert da["window"] == "30d"
    site = da["sites"][0]
    assert site["site_label"] == "DC"
    assert site["history_sufficient"] is False
    assert len(site["collector_gaps"]) == 1
    dev = site["devices"][0]
    assert dev["availability_pct"] == 99.9 and dev["reboot_count"] == 0
    assert dev["booted"] != "—"              # boot_time_ms formatted, not dropped


def test_r04_sla_compliance_by_link_type():
    """R-04: each link is judged against ITS type's ceiling; breach = value over threshold."""
    thr = {"wan": {"latency": 100, "jitter": 30, "packet_loss": 1},
           "mpls": {"latency": 50, "jitter": 20, "packet_loss": 0.5}}

    # WAN link over the latency ceiling → Breached, only latency flagged.
    wan = _sla_link_compliance("WAN", avg_latency=140, avg_jitter=5, avg_packet_loss=0.2, thresholds=thr)
    assert wan["sla_compliance"] == "Breached"
    assert (wan["latency_breached"], wan["jitter_breached"], wan["packet_loss_breached"]) == (True, False, False)

    # MPLS link within its (stricter) ceilings → Met. Same numbers a WAN link would pass too,
    # but MPLS uses the mpls row, proving the type routing.
    mpls = _sla_link_compliance("MPLS", avg_latency=40, avg_jitter=10, avg_packet_loss=0.3, thresholds=thr)
    assert mpls["sla_compliance"] == "Met"

    # A metric with no threshold is never a breach.
    partial = _sla_link_compliance("WAN", avg_latency=999, avg_jitter=999, avg_packet_loss=0.0,
                                   thresholds={"wan": {"packet_loss": 1}})
    assert partial["latency_breached"] is False and partial["sla_compliance"] == "Met"


def test_r04_legacy_fallback_without_thresholds():
    """No thresholds (R-07/R-08 have no form) → legacy 'packet loss ≥ 1%' rule, unchanged."""
    breached = _sla_link_compliance("WAN", 10, 1, 2.0, thresholds=None)
    assert breached["sla_compliance"] == "Breached" and breached["has_thresholds"] is False
    met = _sla_link_compliance("WAN", 10, 1, 0.5, thresholds=None)
    assert met["sla_compliance"] == "Met"


@pytest.mark.asyncio
async def test_r10_bad_window_falls_back_to_24h(monkeypatch):
    """A table_interval that isn't a valid availability window must not reach the query."""
    async def fake_availability(site_name, window, **kwargs):
        return {"summary": {"collector_gaps": []}, "devices": []}

    monkeypatch.setattr(device_uptime, "device_availability", fake_availability)
    ctx = await build_report_context(
        report_type="R-10", gte_ms=0, lte_ms=1, sites=["Site_FGT-DC"], table_interval="15m",
    )
    assert ctx["report_data"]["device_availability"]["window"] == "24h"
