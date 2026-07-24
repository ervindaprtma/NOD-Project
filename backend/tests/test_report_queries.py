"""
Guards the two regressions that silently emptied R-03 and R-09.

Both were contract drifts between modules, not logic bugs — the kind a type
checker misses because the call sites use dynamic dicts.
"""
import inspect

from app.api.interface_stats import _compute_throughput_timeline
from app.opensearch import ipsec, sslvpn, traffic_inbound, traffic_internal
from app.services.report_generator import _auto_interval_str


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
