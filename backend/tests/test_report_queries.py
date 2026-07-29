"""
Guards the two regressions that silently emptied R-03 and R-09.

Both were contract drifts between modules, not logic bugs — the kind a type
checker misses because the call sites use dynamic dicts.
"""
import inspect

import pytest

from app.api.interface_stats import _compute_throughput_timeline
from app.opensearch import device_uptime, ipsec, sslvpn, traffic_inbound, traffic_internal
from app.services.report_generator import _auto_interval_str, _report_title, build_report_context


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
